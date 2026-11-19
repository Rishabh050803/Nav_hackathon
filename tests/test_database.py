"""Exercise migrations and relationships against an isolated SQLite database."""
import os
from pathlib import Path
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

# Configure before importing the application's engine; never touch prepx.db.
_temp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite:///{_temp.name}/test.db'
from app.database import SessionLocal, engine
from app.main import app
from app.models import AuthProviderType, Exam, Option, PdfUpload, Question, User, exam_questions


class DatabaseTest(unittest.TestCase):
    def test_migrations_and_constraints(self):
        config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
        command.upgrade(config, 'head')
        self.assertEqual(set(inspect(engine).get_table_names()), {
            'alembic_version', 'app_user', 'pdf_uploads', 'questions',
            'options', 'exams', 'exam_questions',
        })
        command.check(config)
        with SessionLocal() as session:
            user = User(first_name='Test', username='test@example.com', provider_type=AuthProviderType.EMAIL)
            upload = PdfUpload(original_filename='source.pdf', user=user)
            question = Question(question_text='Question?', pdf_upload=upload,
                                options=[Option(label='A', text='Answer', is_correct=True)])
            first = Exam(user=user, pdf_upload=upload, questions=[question])
            second = Exam(user=user, questions=[question])
            session.add_all([first, second])
            session.commit()
            self.assertIsNotNone(upload.uploaded_at)
            self.assertEqual(first.questions[0].options[0].text, 'Answer')
            session.add(User(first_name='Duplicate', username=user.username))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            session.add(PdfUpload(user_id=9999))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            session.delete(user)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            session.delete(first)
            session.commit()
            self.assertIsNotNone(session.get(Question, question.id))
            session.delete(question)
            session.commit()
            self.assertEqual(session.scalar(select(func.count()).select_from(Option)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(exam_questions)), 0)
            self.assertIsNotNone(session.get(Exam, second.id))
            self.assertEqual(session.execute(text('PRAGMA foreign_key_check')).all(), [])
        command.downgrade(config, 'base')
        self.assertEqual(inspect(engine).get_table_names(), ['alembic_version'])
        command.upgrade(config, 'head')
        command.check(config)
        self.assertFalse(any(getattr(route, 'path', '').startswith('/api') for route in app.routes))


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        engine.dispose()
        _temp.cleanup()
