"""Generation API regression tests after modularizing the feature."""
import unittest
from unittest.mock import patch

from support import migrate
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.auth import mint_token
from app.database import Base, SessionLocal, engine
from app.generation.llm import GenerationUnavailable
from app.main import app
from app.models import Exam, Option, Question, User


class GenerationTest(unittest.TestCase):
    def setUp(self):
        migrate()
        self.clear()
        config = patch.dict('os.environ', {'EXAM_API_KEY': 'generation-test-key'})
        config.start()
        self.addCleanup(config.stop)
        with SessionLocal() as session:
            user = User(first_name='Generator', username='generator')
            other = User(first_name='Other', username='other')
            question = Question(question_text='What is two plus two?', difficulty='easy', topic='Math',
                                options=[Option(label='A', text='Four', is_correct=True),
                                         Option(label='B', text='Five', is_correct=False)])
            session.add_all([user, other, question])
            session.commit()
            self.uid, self.other = user.id, other.id
        self.client = TestClient(app)
        self.headers = {'X-Exam-Api-Key': 'generation-test-key', 'X-User-Id': str(self.uid)}
        self.body = {'question_count': 1, 'per_question_time_seconds': 60, 'difficulty': 'easy'}

    def clear(self):
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(delete(table))

    def tearDown(self):
        self.client.close()
        self.clear()

    def test_bank_paper_save_fetch_and_both_routers_registered(self):
        response = self.client.post('/api/exams/generate', json=self.body, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        paper = response.json()
        self.assertEqual(paper['question_count'], 1)
        self.assertEqual(paper['total_time_seconds'], 60)
        self.assertEqual(paper['questions'][0]['source'], 'previous_year')
        self.assertNotIn('is_correct', response.text)
        self.assertNotIn('bank_question_id', response.text)
        fetched = self.client.get(f"/api/exams/{paper['id']}", headers=self.headers)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json(), paper)
        paths = self.client.get('/openapi.json').json()['paths']
        self.assertIn('/api/exams/generate', paths)
        self.assertIn('/api/attempts/{attempt_id}/pause', paths)
        self.assertIn('/api/exams/{exam_id}/attempt', paths)

    def test_model_adapter_is_used_and_output_is_saved(self):
        body = {**self.body, 'question_count': 2, 'generated_question_count': 1}
        output = [{'question_text': 'What is three plus three?',
                   'options': ['Six', 'Seven', 'Eight', 'Nine'], 'correct_index': 0}]
        with patch('app.generation.service.generate_questions', return_value=output) as model:
            response = self.client.post('/api/exams/generate', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(len(model.call_args.args[0]), 1)
        paper = response.json()
        self.assertEqual(len(paper['questions']), 2)
        self.assertEqual({q['source'] for q in paper['questions']}, {'generated', 'previous_year'})
        self.assertNotIn('is_correct', response.text)
        fetched = self.client.get(f"/api/exams/{paper['id']}", headers=self.headers)
        self.assertEqual(fetched.json(), paper)

        bearer = {'Authorization': f'Bearer {mint_token(self.uid)}'}
        attempt_response = self.client.post(f"/api/exams/{paper['id']}/attempt", headers=bearer)
        self.assertEqual(attempt_response.status_code, 200, attempt_response.text)
        attempt = attempt_response.json()
        self.assertEqual(
            [q['question_text'] for q in attempt['questions']],
            [q['question_text'] for q in paper['questions']],
        )
        path = f"/api/attempts/{attempt['id']}"
        bank = next(q for q in attempt['questions'] if q['source_question_id'] > 0)
        answer = self.client.put(f"{path}/questions/{bank['id']}/answer", headers=bearer, json={
            'version': attempt['version'], 'option_id': bank['options'][1]['id'],
        })
        self.assertEqual(answer.status_code, 200, answer.text)
        submitted = self.client.post(f'{path}/submit', headers=bearer, json={'version': answer.json()['version']})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        report = self.client.get(f'{path}/analysis', headers=bearer)
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()['weak_topics'], ['Math'])
        self.assertEqual({q['status'] for q in report.json()['questions']}, {'incorrect', 'unattempted'})
        self.assertEqual(report.json()['incorrect_count'], 1)

    def test_invalid_model_saves_no_partial_paper(self):
        body = {**self.body, 'generated_question_count': 1}
        for error, expected in ((GenerationUnavailable(), 503), (None, 502)):
            with self.subTest(expected=expected):
                with patch('app.generation.service.generate_questions',
                           side_effect=error, return_value=[{'invalid': True}]):
                    response = self.client.post('/api/exams/generate', json=body, headers=self.headers)
                self.assertEqual(response.status_code, expected, response.text)
                with SessionLocal() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(Exam)), 0)

    def test_access_validation_and_owner_scoping(self):
        response = self.client.post('/api/exams/generate', json=self.body)
        self.assertEqual(response.status_code, 401)
        response = self.client.post('/api/exams/generate', json={**self.body, 'question_count': 0}, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        response = self.client.post('/api/exams/generate', json=self.body, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        other_headers = {**self.headers, 'X-User-Id': str(self.other)}
        response = self.client.get(f"/api/exams/{response.json()['id']}", headers=other_headers)
        self.assertEqual(response.status_code, 404)
