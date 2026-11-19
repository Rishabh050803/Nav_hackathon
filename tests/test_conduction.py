from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from support import CONFIG, migrate
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import mint_token
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Exam, Option, Question, User, utc_now
from app.conduction import service
from app.conduction.models import Attempt, AttemptEvent
from app.conduction.transactions import transaction
from app.conduction.worker import sweep

T0 = datetime(2026, 1, 1, 12)


class ConductionTest(unittest.TestCase):
    def setUp(self):
        migrate()
        self.clear()
        with SessionLocal() as session:
            owner = User(first_name='Owner', username='owner')
            other = User(first_name='Other', username='other')
            questions = [Question(question_text=f'Q{i}', topic='math', options=[
                Option(label='A', text='yes', is_correct=True),
                Option(label='B', text='no', is_correct=False),
            ]) for i in range(2)]
            exam = Exam(title='Exam', user=owner, total_time_seconds=120, question_count=2,
                        questions=questions)
            session.add_all([exam, other])
            session.commit()
            self.uid, self.other, self.eid = owner.id, other.id, exam.id
        self.client = TestClient(app)
        self.headers = {'Authorization': f'Bearer {mint_token(self.uid)}'}

    def clear(self):
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(delete(table))

    def tearDown(self):
        self.client.close()
        self.clear()

    def start(self, now=T0):
        with transaction() as session:
            attempt = service.start(session, self.eid, self.uid, now)
            return service.view(attempt, now)

    def change(self, state, action, seconds=0, **kwargs):
        now = T0 + timedelta(seconds=seconds)
        with transaction() as session:
            attempt = service.owned(session, state['id'], self.uid, now)
            service.mutate(attempt, action, state['version'], now, **kwargs)
            return service.view(attempt, now)

    def get(self, state, seconds):
        now = T0 + timedelta(seconds=seconds)
        with transaction() as session:
            return service.view(service.owned(session, state['id'], self.uid, now), now)

    def test_answer_changes_navigation_review_and_analysis(self):
        s = self.start()
        q1, q2 = s['questions']
        s = self.change(s, 'answer', 5, question_id=q1['id'], option_id=q1['options'][0]['id'])
        s = self.change(s, 'answer', 7, question_id=q1['id'], option_id=q1['options'][1]['id'])
        s = self.change(s, 'navigate', 10, question_id=q2['id'])
        s = self.change(s, 'review', 15, question_id=q2['id'], marked=True)
        s = self.change(s, 'navigate', 30, question_id=q1['id'])
        s = self.change(s, 'answer', 35, question_id=q1['id'], option_id=None)
        s = self.change(s, 'answer', 36, question_id=q1['id'], option_id=q1['options'][0]['id'])
        s = self.change(s, 'submit', 40)
        self.assertEqual([q['time_spent_ms'] for q in s['questions']], [20000, 20000])
        self.assertEqual(s['questions'][0]['visit_count'], 2)
        self.assertTrue(s['questions'][1]['marked_for_review'])
        self.assertEqual(s['questions'][0]['answer_change_count'], 4)
        self.assertEqual(s['answered_count'], 1)
        with transaction() as session:
            report = service.analysis(service.owned(session, s['id'], self.uid, T0))
            self.assertEqual(report['correct_count'], 1)
            self.assertEqual(report['total_time_spent_ms'], 40000)
            self.assertEqual(len([e for e in report['events'] if e['kind'] == 'answer_saved']), 4)
        self.assertEqual(self.change(s, 'submit', 50)['version'], s['version'])
        with self.assertRaises(service.RuleError):
            self.change(s, 'answer', 50, question_id=q1['id'], option_id=None)

    def test_pause_freezes_time_resume_preserves_budget(self):
        s = self.start()
        s = self.change(s, 'pause', 20)
        self.assertEqual(s['remaining_seconds'], 100)
        self.assertEqual(self.get(s, 300)['remaining_seconds'], 100)
        with self.assertRaises(service.RuleError):
            self.change(s, 'navigate', 100, question_id=s['questions'][1]['id'])
        s = self.change(s, 'resume', 919)  # 899 seconds paused, strictly less than 15 minutes.
        self.assertEqual(s['remaining_seconds'], 100)
        s = self.change(s, 'submit', 929)
        self.assertEqual(s['questions'][0]['time_spent_ms'], 30000)

    def test_pause_at_exact_15_minutes_submits_and_persists_error(self):
        s = self.change(self.start(), 'pause', 20)
        with self.assertRaises(service.RuleError):
            self.change(s, 'resume', 920)
        final = self.get(s, 1000)
        self.assertEqual(final['submission_reason'], 'pause_expired')
        self.assertEqual(final['submitted_at'], T0 + timedelta(seconds=920))
        self.assertEqual(final['questions'][0]['time_spent_ms'], 20000)
        self.assertEqual(final['remaining_seconds'], 0)

    def test_deadline_rejects_answers_at_boundary_and_caps_timing(self):
        s = self.start()
        with self.assertRaises(service.RuleError):
            self.change(s, 'answer', 120, question_id=s['questions'][0]['id'], option_id=None)
        final = self.get(s, 500)
        self.assertEqual(final['submission_reason'], 'time_expired')
        self.assertEqual(final['submitted_at'], T0 + timedelta(seconds=120))
        self.assertEqual(final['questions'][0]['time_spent_ms'], 120000)
        self.assertEqual(self.get(s, 600)['version'], final['version'])

    def test_worker_submits_without_client_and_after_restart(self):
        s = self.start()
        with patch('app.conduction.worker.utc_now', return_value=T0 + timedelta(hours=1)):
            sweep()
            sweep()
        with SessionLocal() as session:
            attempt = session.get(Attempt, s['id'])
            self.assertEqual(attempt.status, 'submitted')
            self.assertEqual(attempt.submitted_at, T0 + timedelta(seconds=120))
            events = list(session.scalars(select(AttemptEvent).where(AttemptEvent.kind == 'submitted')))
            self.assertEqual(len(events), 1)

    def test_retry_start_snapshot_and_fallback_duration(self):
        with SessionLocal() as session:
            exam = session.get(Exam, self.eid)
            exam.total_time_seconds, exam.max_time = 0, 2
            session.commit()
        s = self.start()
        self.assertEqual(s['duration_seconds'], 120)
        self.assertEqual(self.start(T0 + timedelta(seconds=10))['id'], s['id'])
        with SessionLocal() as session:
            q = session.get(Question, s['questions'][0]['source_question_id'])
            q.question_text = 'Edited'
            session.delete(q.options[0])
            session.commit()
        frozen = self.get(s, 20)
        self.assertEqual(frozen['questions'][0]['question_text'], 'Q0')
        self.assertEqual(len(frozen['questions'][0]['options']), 2)
        self.assertNotIn('is_correct', frozen['questions'][0]['options'][0])
        self.assertEqual(self.start(T0 + timedelta(seconds=200))['status'], 'submitted')

    def test_invalid_option_question_and_stale_write_do_not_change_answer(self):
        s = self.start()
        q = s['questions'][0]
        with self.assertRaises(service.RuleError):
            self.change(s, 'answer', 1, question_id=q['id'], option_id=s['questions'][1]['options'][0]['id'])
        with self.assertRaises(service.RuleError):
            self.change(s, 'navigate', 1, question_id=9999)
        saved = self.change(s, 'answer', 2, question_id=q['id'], option_id=q['options'][0]['id'])
        with self.assertRaises(service.RuleError):
            self.change(s, 'answer', 3, question_id=q['id'], option_id=None)
        self.assertEqual(self.get(saved, 3)['questions'][0]['selected_option_id'], q['options'][0]['id'])

    def test_api_authentication_ownership_serialization_and_no_answer_leak(self):
        self.assertEqual(self.client.post(f'/api/exams/{self.eid}/attempt').status_code, 401)
        bad = {'Authorization': 'Bearer broken.token'}
        self.assertEqual(self.client.post(f'/api/exams/{self.eid}/attempt', headers=bad).status_code, 401)
        with patch('app.conduction.router.utc_now', return_value=T0):
            response = self.client.post(f'/api/exams/{self.eid}/attempt', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        s = response.json()
        self.assertTrue(s['deadline'].endswith('Z'))
        self.assertNotIn('is_correct', response.text)
        other = {'Authorization': f'Bearer {mint_token(self.other)}'}
        self.assertEqual(self.client.get(f"/api/attempts/{s['id']}", headers=other).status_code, 404)
        self.assertEqual(self.client.post(f'/api/exams/{self.eid}/attempt', headers=other).status_code, 404)
        with patch('app.conduction.router.utc_now', return_value=T0):
            self.assertEqual(self.client.get(f"/api/attempts/{s['id']}/analysis", headers=self.headers).status_code, 409)
            path = f"/api/attempts/{s['id']}/questions/{s['questions'][0]['id']}/answer"
            self.assertEqual(self.client.put(path, headers=self.headers, json={'version': 1}).status_code, 422)
            result = self.client.put(path, headers=self.headers, json={
                'version': 1, 'option_id': s['questions'][0]['options'][0]['id']})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['answered_count'], 1)
            submitted = self.client.post(f"/api/attempts/{s['id']}/submit", headers=self.headers,
                                         json={'version': result.json()['version']})
            self.assertEqual(submitted.status_code, 200)
            report = self.client.get(f"/api/attempts/{s['id']}/analysis", headers=self.headers)
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(report.json()['correct_count'], 1)

    def test_concurrent_answers_only_one_version_wins(self):
        s = self.start(utc_now())
        q = s['questions'][0]
        path = f"/api/attempts/{s['id']}/questions/{q['id']}/answer"
        def send(option):
            return self.client.put(path, headers=self.headers,
                                   json={'version': s['version'], 'option_id': option['id']}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(send, q['options']))
        self.assertEqual(sorted(statuses), [200, 409])

    def test_heartbeat_checkpoints_without_double_counting(self):
        s = self.start()
        s = self.change(s, 'heartbeat', 10)
        s = self.change(s, 'heartbeat', 20)
        s = self.change(s, 'submit', 30)
        self.assertEqual(s['questions'][0]['time_spent_ms'], 30000)

    def test_invalid_exam_and_analysis_rules(self):
        with SessionLocal() as session:
            exam = session.get(Exam, self.eid)
            exam.total_time_seconds = 0
            session.commit()
        with self.assertRaises(service.RuleError):
            self.start()
        with SessionLocal() as session:
            exam = session.get(Exam, self.eid)
            exam.total_time_seconds = 120
            exam.questions = []
            session.commit()
        with self.assertRaises(service.RuleError):
            self.start()

    def test_second_migration_preserves_existing_exam(self):
        # Existing original entities survive upgrade/downgrade of the new tables.
        command.downgrade(CONFIG, '0001')
        command.upgrade(CONFIG, 'head')
        with SessionLocal() as session:
            self.assertEqual(session.get(Exam, self.eid).title, 'Exam')
        self.assertEqual(self.start()['status'], 'running')

    def test_lifespan_runs_automatic_submission(self):
        import time
        s = self.start()
        with TestClient(app):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with SessionLocal() as session:
                    if session.get(Attempt, s['id']).status == 'submitted':
                        break
                time.sleep(0.01)
            else:
                self.fail('Lifespan worker did not submit an expired exam')

    def test_worker_pause_timeout_and_expired_token(self):
        s = self.change(self.start(), 'pause', 20)
        with patch('app.conduction.worker.utc_now', return_value=T0 + timedelta(seconds=920)):
            sweep()
        final = self.get(s, 921)
        self.assertEqual(final['submission_reason'], 'pause_expired')
        with patch('app.auth.time.time', return_value=0):
            token = mint_token(self.uid, 1)
        result = self.client.get(f"/api/attempts/{s['id']}",
                                 headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(result.status_code, 401)
