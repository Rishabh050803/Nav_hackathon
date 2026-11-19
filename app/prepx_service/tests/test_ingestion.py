import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pymupdf
import pytest
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.prepx_service.config import Settings, get_settings
from app.database import Base, enable_foreign_keys
from app.auth import mint_token
from app.prepx_service.router import get_session, get_service
from app.prepx_service.errors import ServiceError
from app.prepx_service.llm import GroqClient
from app.prepx_service.main import app
from app.models import Exam, Option, PdfUpload, Question, User, exam_questions
from app.prepx_service.ocr import LocalOCR
from app.prepx_service.pdf import inspect_pdf, read_pdf
from app.prepx_service.schemas import AnswerBatch, ExtractedDocument
from app.prepx_service.service import IngestionService, relative_levels


def pdf_bytes(text='1. What is 2 + 2?\nA. 3\nB. 4\nChoose the correct option.', pages=1, image=False):
    with pymupdf.open() as doc:
        for _ in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), text)
            if image:
                import io
                stream = io.BytesIO()
                Image.new('RGB', (40, 40), 'white').save(stream, format='PNG')
                page.insert_image(pymupdf.Rect(72, 140, 112, 180), stream=stream.getvalue())
        return doc.tobytes()


def document(answer=False):
    return ExtractedDocument.model_validate({'examname': 'Demo PYQ', 'questions': [{
        'original_question_number': '1', 'question_text': 'What is 2 + 2?',
        'options': [{'label': 'A', 'text': '3'}, {'label': 'B', 'text': '4'}],
        'topic': 'Arithmetic', 'languages': ['en'], 'source_pages': [1],
        'correct_labels': ['B'] if answer else [], 'difficulty_score': .4, 'warnings': []}]})


class FakeGroq:
    def __init__(self, answer_present=False):
        self.answer_present = answer_present
        self.answer_calls = 0
        self.extract_calls = 0

    def extract(self, pages, hints):
        self.extract_calls += 1
        assert 'SOURCE PAGE 1' in pages
        return document(self.answer_present)

    def answer(self, questions):
        self.answer_calls += 1
        return AnswerBatch.model_validate({'answers': [dict(question_index=q['question_index'],
            correct_labels=['B'], warning=None) for q in questions]})


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + (tmp_path / 'test.db').as_posix())
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'integration-test-secret-at-least-32-characters')
    get_settings.cache_clear()
    import app.database as db
    import app.auth as auth
    import app.prepx_service.router as routes
    import app.conduction.transactions as transactions
    engine = create_engine('sqlite:///' + (tmp_path / 'test.db').as_posix(), connect_args={'check_same_thread': False})
    event.listen(engine, 'connect', enable_foreign_keys)
    factory = sessionmaker(engine, autoflush=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', factory)
    monkeypatch.setattr(auth, 'SessionLocal', factory)
    monkeypatch.setattr(routes, 'SessionLocal', factory)
    monkeypatch.setattr(transactions, 'SessionLocal', factory)
    config = Config(str(Path(__file__).resolve().parents[3] / 'alembic.ini'))
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[3] / 'migrations'))
    command.upgrade(config, 'head')
    with Session(engine, expire_on_commit=False) as session:
        session.add(User(id=1, first_name='Test', username='test'))
        session.commit()
        yield session, config, engine
    engine.dispose()
    get_settings.cache_clear()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_migrations_match_models_and_roundtrip(database):
    session, config, engine = database
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
    session.close()
    command.downgrade(config, 'base')
    assert 'questions' not in inspect(engine).get_table_names()
    command.upgrade(config, 'head')
    assert 'question_extraction_metadata' not in inspect(engine).get_table_names()


def test_ingest_generates_missing_answer_and_saves_all_tables(database):
    session, _, _ = database
    groq = FakeGroq()
    service = IngestionService(get_settings(), llm=groq)
    result = service.ingest(session, [('exam.pdf', pdf_bytes())], 1)
    q = result.documents[0].questions[0]
    assert q.correct_answer_text == '4'
    assert q.answer_source == 'groq'
    assert q.level == 'medium'
    assert count(session, PdfUpload) == count(session, Exam) == count(session, Question) == 1
    assert count(session, Option) == 2
    assert count(session, exam_questions) == 1
    assert q.languages == ['en']
    assert 'question_extraction_metadata' not in inspect(database[2]).get_table_names()
    assert session.scalars(select(Option.is_correct).order_by(Option.id)).all() == [False, True]


def test_source_answer_is_preserved_without_solver(database):
    session, _, _ = database
    groq = FakeGroq(answer_present=True)
    result = IngestionService(get_settings(), llm=groq).ingest(session, [('exam.pdf', pdf_bytes())], 1)
    assert result.documents[0].questions[0].answer_source == 'pdf'
    assert groq.answer_calls == 0


def test_multiple_pdfs_append_to_owned_exam(database):
    session, _, _ = database
    session.add(Exam(id=42, title='Existing exam', user_id=1))
    session.commit()
    result = IngestionService(get_settings(), llm=FakeGroq()).ingest(session,
        [('a.pdf', pdf_bytes()), ('b.pdf', pdf_bytes())], 1, exam_id=42)
    assert len(result.documents) == 2
    assert {d.exam_id for d in result.documents} == {42}
    assert session.get(Exam, 42).question_count == 2
    assert count(session, PdfUpload) == 2


def test_second_pdf_failure_saves_nothing(database):
    session, _, _ = database
    groq = FakeGroq()
    original = groq.extract
    def fail_second(*args):
        if groq.extract_calls == 1:
            raise ServiceError('Upstream failure', 503)
        return original(*args)
    groq.extract = fail_second
    with pytest.raises(ServiceError):
        IngestionService(get_settings(), llm=groq).ingest(session, [('a.pdf', pdf_bytes()), ('b.pdf', pdf_bytes())], 1)
    assert count(session, PdfUpload) == count(session, Question) == 0


def test_database_failure_rolls_back_flushed_rows(database, monkeypatch):
    session, _, _ = database
    def fail_commit():
        raise SQLAlchemyError('simulated commit failure')
    monkeypatch.setattr(session, 'commit', fail_commit)
    with pytest.raises(SQLAlchemyError):
        IngestionService(get_settings(), llm=FakeGroq()).ingest(session, [('a.pdf', pdf_bytes())], 1)
    assert count(session, PdfUpload) == count(session, Question) == count(session, Exam) == 0


@pytest.mark.parametrize('labels', [['Z'], ['B', 'B']])
def test_invalid_generated_answer_rejected(database, labels):
    session, _, _ = database
    groq = FakeGroq()
    groq.answer = lambda _: AnswerBatch.model_validate({'answers': [dict(question_index=0, correct_labels=labels, warning=None)]})
    with pytest.raises(ServiceError, match='outside'):
        IngestionService(get_settings(), llm=groq).ingest(session, [('a.pdf', pdf_bytes())], 1)
    assert count(session, Question) == 0


def test_unresolved_answer_preserves_null_correctness(database):
    session, _, _ = database
    groq = FakeGroq()
    groq.answer = lambda _: AnswerBatch.model_validate({'answers': [dict(question_index=0, correct_labels=[], warning='Uncertain')]})
    result = IngestionService(get_settings(), llm=groq).ingest(session, [('a.pdf', pdf_bytes())], 1)
    assert result.documents[0].questions[0].correct_answer_text is None
    assert result.documents[0].questions[0].answer_source == 'unresolved'
    assert session.scalars(select(Option.is_correct)).all() == [None, None]


def test_exam_ownership_checked_before_llm(database):
    session, _, _ = database
    session.add(User(id=2, first_name='Other', username='other'))
    session.flush()
    session.add(Exam(id=99, title='Other', user_id=2))
    session.commit()
    groq = FakeGroq()
    with pytest.raises(ServiceError, match='this user'):
        IngestionService(get_settings(), llm=groq).ingest(session, [('a.pdf', pdf_bytes())], 1, exam_id=99)
    assert groq.extract_calls == 0


def test_shared_question_deletion_uses_main_schema(database):
    session, _, engine = database
    result = IngestionService(get_settings(), llm=FakeGroq()).ingest(session, [('a.pdf', pdf_bytes())], 1)
    question_id = result.documents[0].questions[0].question_id
    other = Exam(title='Other', user_id=1, questions=[session.get(Question, question_id)])
    session.add(other)
    session.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql('DELETE FROM exams WHERE id = ?', (result.documents[0].exam_id,))
    assert count(session, Question) == 1
    with engine.begin() as connection:
        connection.exec_driver_sql('DELETE FROM questions WHERE id = ?', (question_id,))
    session.expire_all()
    assert count(session, Option) == count(session, exam_questions) == 0


def test_pdf_validation_limits_and_native_text():
    settings = Settings(_env_file=None)
    with pytest.raises(ServiceError, match='not a PDF'):
        inspect_pdf(b'hello', settings)
    with pytest.raises(ServiceError, match='Each PDF must contain'):
        inspect_pdf(pdf_bytes(pages=11), settings)
    source = read_pdf('test.pdf', pdf_bytes(), settings, LocalOCR(settings), None)
    assert not source.pages[0].used_ocr
    assert '2 + 2' in source.prompt_text()
    with pymupdf.open(stream=pdf_bytes(), filetype='pdf') as doc:
        encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='secret', owner_pw='owner')
    with pytest.raises(ServiceError, match='Password'):
        inspect_pdf(encrypted, settings)


def test_mixed_image_page_runs_ocr():
    class FakeOCR:
        calls = 0
        def recognize(self, image, languages, backend):
            self.calls += 1
            assert image.width > 0
            return '1. Question from raster\nA. one\nB. two'
    ocr = FakeOCR()
    source = read_pdf('mixed.pdf', pdf_bytes(image=True), Settings(_env_file=None), ocr, ['en'])
    assert ocr.calls == 1
    assert source.pages[0].used_ocr
    assert source.warnings


def test_math_regions_are_merged_in_reading_order(monkeypatch):
    import app.prepx_service.ocr as module
    monkeypatch.setattr(module, 'formula_regions', lambda image: [((50, 10, 90, 30), 'x^2')])
    monkeypatch.setattr(module.pytesseract, 'image_to_data', lambda *args, **kwargs: {
        'text': ['Solve', 'now'], 'left': [0, 100], 'top': [10, 10], 'height': [20, 20]})
    ocr = LocalOCR(Settings(_env_file=None, math_ocr_enabled=True))
    monkeypatch.setattr(ocr, 'installed', lambda: {'eng', 'fra'})
    assert ocr.recognize(Image.new('RGB', (200, 100), 'white'), ['en']) == 'Solve $x^2$ now'


def test_unsupported_or_missing_models_fail(monkeypatch):
    ocr = LocalOCR(Settings(_env_file=None))
    monkeypatch.setattr(ocr, 'installed', lambda: {'eng', 'fra'})
    with pytest.raises(ServiceError, match='Missing'):
        ocr.recognize(Image.new('RGB', (10, 10)), ['hi'])
    with pytest.raises(ServiceError, match='unsupported'):
        ocr.recognize(Image.new('RGB', (10, 10)), ['sat'])
    assert next(c for c in ocr.capabilities() if c['code'] == 'en')['installed']


def test_relative_difficulty_ties():
    assert relative_levels([.1, .5, .9]) == ['easy', 'medium', 'difficult']
    assert relative_levels([.5, .5, .5]) == ['medium'] * 3


def test_structured_groq_transport_and_truncation():
    def handler(request):
        body = json.loads(request.content)
        assert body['response_format']['json_schema']['strict']
        assert body['messages'][0]['role'] == 'system'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': document().model_dump_json()}}]})
    client = GroqClient(Settings(_env_file=None, groq_api_key='test'), httpx.MockTransport(handler))
    assert client.extract('PDF text', None).questions[0].options[1].text == '4'
    client.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'finish_reason': 'length'}]}))
    with pytest.raises(ServiceError, match='incomplete'):
        client.extract('PDF text', None)


@pytest.fixture
def api(database):
    session, _, _ = database
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_service] = lambda: IngestionService(get_settings(), llm=FakeGroq())
    with TestClient(app, headers={'Authorization': f'Bearer {mint_token(1)}'}) as client:
        yield client
    app.dependency_overrides.clear()


def test_api_multiple_pdf_uploads(api):
    response = api.post('/api/v1/ingestions', data={'user_id': '1', 'languages': 'en'}, files=[
        ('files', ('a.pdf', pdf_bytes(), 'application/pdf')),
        ('files', ('b.pdf', pdf_bytes(), 'application/pdf'))])
    assert response.status_code == 200, response.text
    assert len(response.json()['documents']) == 2


@pytest.mark.parametrize('filename,content,language', [('a.png', b'PNG', 'en'), ('a.pdf', b'fake', 'en'), ('a.pdf', b'fake', 'fr')])
def test_api_rejects_invalid_inputs(api, filename, content, language):
    response = api.post('/api/v1/ingestions', data={'user_id': '1', 'languages': language}, files={'files': (filename, content)})
    assert response.status_code == 422


def test_api_auth(api):
    assert api.get('/api/v1/languages', headers={'Authorization': ''}).status_code == 401
    assert api.get('/api/v1/languages').status_code == 200


def test_api_uses_authenticated_owner_not_form_id(api, database):
    response = api.post('/api/v1/ingestions', data={'user_id': '999'},
                        files={'files': ('a.pdf', pdf_bytes())})
    assert response.status_code == 200, response.text
    assert database[0].scalar(select(PdfUpload.user_id)) == 1


def test_imports_share_main_models_and_metadata():
    from app.prepx_service import service
    from app.models import Question as MainQuestion
    assert service.Question is MainQuestion
    assert service.Question.metadata is Base.metadata
    assert not hasattr(get_settings(), 'database_url')
    assert not hasattr(get_settings(), 'service_api_key')
    paths = app.openapi()['paths']
    assert '/api/v1/ingestions' in paths
    assert '/api/exams/{exam_id}/attempt' in paths


def test_api_prevalidates_whole_batch(api, database):
    response = api.post('/api/v1/ingestions', data={'user_id': '1'}, files=[
        ('files', ('good.pdf', pdf_bytes())), ('files', ('bad.pdf', b'bad'))])
    assert response.status_code == 422
    assert count(database[0], PdfUpload) == 0


def test_ingestion_questions_are_available_to_main_conduction(api, database):
    session, _, _ = database
    session.add(Exam(id=101, title='Canonical title', user_id=1, total_time_seconds=120))
    session.commit()
    parsed = api.post('/api/v1/ingestions', data={'exam_id': '101', 'examname': 'Ignored override'},
                      files={'files': ('a.pdf', pdf_bytes())})
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()['documents'][0]['questions'][0]['examname'] == 'Canonical title'
    assert session.get(Exam, 101).title == 'Canonical title'
    session.rollback()
    started = api.post('/api/exams/101/attempt')
    assert started.status_code == 200, started.text
    question = started.json()['questions'][0]
    assert question['source_question_id'] == parsed.json()['documents'][0]['questions'][0]['question_id']
    assert question['question_text'] == 'What is 2 + 2?'
    assert all('is_correct' not in option for option in question['options'])
