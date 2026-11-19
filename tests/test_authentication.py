import unittest

from support import migrate
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Option, Question, User


class AuthenticationTest(unittest.TestCase):
    def setUp(self):
        migrate()
        self.clear()
        self.addCleanup(self.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.credentials = {'username': 'demo-user', 'password': 'use-a-long-demo-password'}

    def clear(self):
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(delete(table))

    def register(self, **fields):
        return self.client.post('/api/auth/register', json={
            'first_name': 'Demo', **self.credentials, **fields,
        })

    def test_registration_login_and_bearer_exam_flow(self):
        created = self.register()
        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotIn('password', created.json())
        self.assertEqual(self.register().status_code, 409)

        with SessionLocal.begin() as session:
            user = session.scalar(select(User).where(User.username == self.credentials['username']))
            self.assertTrue(user.password.startswith('scrypt-v1$'))
            self.assertNotIn(self.credentials['password'], user.password)
            session.add(Question(question_text='Which answer is correct?', topic='Math', difficulty='easy',
                                 options=[Option(label='A', text='Correct', is_correct=True),
                                          Option(label='B', text='Incorrect', is_correct=False)]))

        login = self.client.post('/api/auth/login', json=self.credentials)
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.headers['cache-control'], 'no-store')
        headers = {'Authorization': f"Bearer {login.json()['access_token']}"}
        self.assertEqual(self.client.get('/api/auth/me', headers=headers).json(), created.json())
        paper = self.client.post('/api/exams/generate', headers=headers, json={
            'question_count': 1, 'per_question_time_seconds': 120, 'difficulty': 'easy',
        })
        self.assertEqual(paper.status_code, 201, paper.text)
        exam_path = f"/api/exams/{paper.json()['id']}"
        self.assertEqual(self.client.get(exam_path, headers=headers).json(), paper.json())
        attempt = self.client.post(f'{exam_path}/attempt', headers=headers)
        self.assertEqual(attempt.status_code, 200, attempt.text)
        path = f"/api/attempts/{attempt.json()['id']}"
        submit = self.client.post(f'{path}/submit', headers=headers, json={'version': attempt.json()['version']})
        self.assertEqual(submit.status_code, 200, submit.text)
        for endpoint in ['evaluation', 'analysis']:
            result = self.client.get(f'{path}/{endpoint}', headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['unattempted_count'], 1)

    def test_invalid_credentials_and_tokens(self):
        invalid_password = self.register(password='abc123')
        self.assertEqual(invalid_password.status_code, 422)
        self.assertNotIn('abc123', invalid_password.text)
        self.assertEqual(self.register().status_code, 201)
        for fields in [{'password': 'wrong-password'}, {'username': 'unknown'}]:
            response = self.client.post('/api/auth/login', json={**self.credentials, **fields})
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json()['detail'], 'Invalid username or password.')
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        invalid = {'Authorization': 'Bearer malformed'}
        self.assertEqual(self.client.get('/api/auth/me', headers=invalid).status_code, 401)
        self.assertEqual(self.client.get('/api/exams/1', headers=invalid).status_code, 401)
