"""History statistics and AI boundary tests using isolated exam snapshots."""
from datetime import timedelta
import json
import unittest
from unittest.mock import MagicMock, patch

from support import migrate
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.auth import mint_token
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Exam, User, utc_now
from app.conduction.models import Attempt, AttemptQuestion
from app.exam_analysis import llm
from app.exam_analysis.schemas import CoachingAdvice


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        migrate()
        self.clear()
        with SessionLocal() as session:
            user = User(first_name='Private Name', username='private@example.com')
            other = User(first_name='Other', username='other')
            session.add_all([user, other])
            session.commit()
            self.uid, self.other = user.id, other.id
        self.now = utc_now()
        self.client = TestClient(app)
        self.headers = {'Authorization': f'Bearer {mint_token(self.uid)}'}
        config = patch.dict('os.environ', {
            'ANALYSIS_LLM_URL': '', 'ANALYSIS_LLM_MODEL': '', 'ANALYSIS_LLM_API_KEY': '',
            'EXAM_LLM_URL': '', 'EXAM_LLM_MODEL': '', 'EXAM_LLM_API_KEY': '',
        })
        config.start()
        self.addCleanup(config.stop)

    def clear(self):
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(delete(table))

    def tearDown(self):
        self.client.close()
        self.clear()

    def add_attempt(self, rows, user_id=None, days_ago=1, status='submitted'):
        user_id = user_id or self.uid
        with SessionLocal() as session:
            exam = Exam(user_id=user_id, title='Private exam title', total_time_seconds=100)
            session.add(exam)
            session.flush()
            started = self.now - timedelta(days=days_ago)
            attempt = Attempt(exam_id=exam.id, user_id=user_id, title=exam.title,
                started_at=started, deadline=started + timedelta(seconds=100), duration_seconds=100,
                status=status, submitted_at=started + timedelta(seconds=50) if status == 'submitted' else None,
                submission_reason='manual' if status == 'submitted' else None,
                questions=[AttemptQuestion(source_question_id=i + 1, position=i + 1,
                    question_text='PRIVATE QUESTION', topic=topic, difficulty='easy' if i % 2 else 'hard',
                    options=[dict(id=1, label='A', text='PRIVATE ANSWER', is_correct=True),
                             dict(id=2, label='B', text='Wrong', is_correct=False)],
                    selected_option_id=1 if result == 'correct' else 2 if result == 'incorrect' else None,
                    time_spent_ms=1000, visit_count=2 if i == 0 else 1, answer_change_count=1 if result != 'unattempted' else 0,
                    marked_for_review=False) for i, (topic, result) in enumerate(rows)])
            session.add(attempt)
            session.commit()
            return attempt.id

    def seed_history(self):
        self.add_attempt([('Algebra', 'correct')] * 5 + [('Geometry', 'correct')] +
                         [('Geometry', 'incorrect')] * 4 + [('Skipped', 'unattempted')], days_ago=2)
        self.add_attempt([(' algebra ', 'correct')] * 4 + [('Algebra', 'incorrect')] +
                         [('Geometry', 'correct')] * 2 + [('Geometry', 'incorrect')] * 3 +
                         [('Skipped', 'unattempted')])

    def performance(self):
        result = self.client.get('/api/analysis/performance', headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def advice(self):
        result = self.client.post('/api/analysis/suggestions', headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_empty_history_has_no_invented_percentages_or_ai_call(self):
        data = self.performance()
        self.assertEqual(data['submitted_exam_count'], 0)
        self.assertIsNone(data['overall']['accuracy_percent'])
        self.assertIsNone(data['overall']['score_percent'])
        self.assertIsNone(data['overall']['accuracy_interval_95'])
        self.assertEqual(data['strong_topics'], [])
        with patch('app.exam_analysis.llm.generate_advice') as model:
            advice = self.advice()
        model.assert_not_called()
        self.assertEqual(advice['fallback_reason'], 'insufficient_history')

    def test_statistics_classification_normalization_and_privacy(self):
        self.seed_history()
        self.add_attempt([('Other-only', 'correct')] * 10, user_id=self.other)
        self.add_attempt([('Running-only', 'correct')], days_ago=0, status='running')
        data = self.performance()
        self.assertEqual(data['submitted_exam_count'], 2)
        self.assertEqual(data['overall']['total_questions'], 22)
        self.assertEqual(data['overall']['answered_count'], 20)
        self.assertEqual(data['overall']['correct_count'], 12)
        self.assertEqual(data['overall']['accuracy_percent'], 60)
        self.assertEqual(data['overall']['score_percent'], 54.55)
        self.assertEqual(data['overall']['completion_percent'], 90.91)
        self.assertEqual(data['strong_topics'], ['Algebra'])
        self.assertEqual(data['weak_topics'], ['Geometry'])
        self.assertEqual(data['overall']['total_time_spent_ms'], 22000)
        self.assertEqual(data['pacing']['focus_budget_utilization_percent'], 11)
        low, high = data['overall']['accuracy_interval_95']
        self.assertLess(low, 60)
        self.assertGreater(high, 60)
        self.assertEqual(data['by_difficulty']['easy']['total_questions'] +
                         data['by_difficulty']['hard']['total_questions'], 22)
        self.assertEqual(data['trend']['direction'], 'insufficient_data')
        self.assertTrue(data['generated_at'].endswith('Z'))
        self.assertTrue(data['timeline'][0]['submitted_at'].endswith('Z'))
        for private in ('Other-only', 'Running-only', 'PRIVATE QUESTION', 'PRIVATE ANSWER', 'private@example.com'):
            self.assertNotIn(private, json.dumps(data))

    def test_small_samples_and_all_unanswered_are_not_strong_or_weak(self):
        self.add_attempt([('SingleExam', 'correct')] * 5 + [('Skipped', 'unattempted')] * 5)
        data = self.performance()
        self.assertEqual(data['strong_topics'], [])
        self.assertEqual(data['weak_topics'], [])
        self.assertTrue(all(t['classification'] == 'insufficient_data' for t in data['topics']))
        self.assertEqual(data['overall']['accuracy_percent'], 100)
        self.assertEqual(data['overall']['score_percent'], 50)
        self.assertEqual(data['overall']['completion_percent'], 50)

    def test_trend_and_variability_are_calculated_chronologically(self):
        for index in range(6):
            self.add_attempt([('Math', 'correct' if index else 'incorrect')], days_ago=7 - index)
        data = self.performance()
        self.assertEqual(data['trend']['recent_exam_count'], 5)
        self.assertEqual(data['trend']['previous_exam_count'], 1)
        self.assertEqual(data['trend']['score_change_percentage_points'], 100)
        self.assertEqual(data['trend']['direction'], 'improving')
        self.assertEqual(data['current_80_percent_score_streak'], 5)
        self.assertEqual(data['median_exam_score_percent'], 100)
        self.assertGreater(data['score_standard_deviation'], 0)
        dates = [t['submitted_at'] for t in data['timeline']]
        self.assertEqual(dates, sorted(dates))

    def test_missing_configuration_returns_actionable_labelled_rules(self):
        self.seed_history()
        data = self.advice()
        self.assertEqual(data['source'], 'rules')
        self.assertEqual(data['fallback_reason'], 'not_configured')
        recommendations = data['advice']['recommendations']
        self.assertEqual(recommendations[0]['topic'], 'Geometry')
        self.assertEqual(recommendations[0]['priority'], 'high')
        self.assertTrue(any(r['topic'] == 'Algebra' for r in recommendations))

    def model_advice(self, payload):
        weak = next(f for f in payload['findings'] if f['category'] == 'weak')
        return CoachingAdvice(overall_assessment='Algebra is strong; Geometry needs targeted practice.',
            recommendations=[dict(priority='high', topic=weak['topic'], action='Review geometry concepts and practise five questions.',
                rationale=weak['message'], evidence_ids=[weak['id']])])

    def test_ai_uses_only_aggregate_evidence_without_holding_sqlite_lock(self):
        self.seed_history()
        def model(payload):
            # A separate connection can write while the model is running.
            with engine.connect() as connection:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                connection.rollback()
            serialized = json.dumps(payload)
            for private in ('PRIVATE QUESTION', 'PRIVATE ANSWER', 'private@example.com', 'Private Name', 'exam_id', 'attempt_id'):
                self.assertNotIn(private, serialized)
            return self.model_advice(payload)
        with patch('app.exam_analysis.llm.configured', return_value=True), \
             patch('app.exam_analysis.llm.generate_advice', side_effect=model) as model_call:
            data = self.advice()
        self.assertEqual(data['source'], 'ai')
        self.assertIsNone(data['fallback_reason'])
        self.assertEqual(model_call.call_count, 1)
        self.assertEqual(data['performance']['overall']['correct_count'], 12)

    def test_invalid_evidence_and_model_failure_fall_back(self):
        self.seed_history()
        fabricated = CoachingAdvice(overall_assessment='Fabricated', recommendations=[dict(
            priority='high', topic='Unknown', action='Practise.', rationale='Unsupported.', evidence_ids=['invented'])])
        for error, output, expected in [
            (llm.AdviceUnavailable(), None, 'unavailable'),
            (llm.InvalidAdvice(), None, 'invalid_response'),
            (None, fabricated, 'invalid_response'),
        ]:
            with self.subTest(expected=expected), patch('app.exam_analysis.llm.configured', return_value=True), \
                 patch('app.exam_analysis.llm.generate_advice', side_effect=error, return_value=output):
                data = self.advice()
                self.assertEqual(data['source'], 'rules')
                self.assertEqual(data['fallback_reason'], expected)
                self.assertEqual(data['performance']['overall']['correct_count'], 12)

    def test_http_adapter_validates_success_malformed_and_oversized_output(self):
        self.seed_history()
        advice = self.model_advice(self.performance())
        response = MagicMock()
        response.__enter__.return_value = response
        config = {'ANALYSIS_LLM_URL': 'https://model.example.test/chat/completions', 'ANALYSIS_LLM_MODEL': 'test-model'}
        with patch.dict('os.environ', config), patch('app.exam_analysis.llm.urlopen', return_value=response) as call:
            response.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop',
                'message': {'content': advice.model_dump_json()}}]}).encode()
            result = llm.generate_advice({'findings': []})
            self.assertEqual(result, advice)
            self.assertEqual(call.call_args.kwargs['timeout'], 30)
            response.read.return_value = b'bad json'
            with self.assertRaises(llm.InvalidAdvice):
                llm.generate_advice({})
            response.read.return_value = b'x' * 128001
            with self.assertRaises(llm.InvalidAdvice):
                llm.generate_advice({})

    def test_history_settles_overdue_attempt_and_auth_is_required(self):
        attempt_id = self.add_attempt([('Math', 'correct')], days_ago=1, status='running')
        self.assertEqual(self.client.get('/api/analysis/performance').status_code, 401)
        self.assertEqual(self.client.post('/api/analysis/suggestions').status_code, 401)
        data = self.performance()
        self.assertEqual(data['submitted_exam_count'], 1)
        self.assertEqual(data['submission_reasons'], {'time_expired': 1})
        with SessionLocal() as session:
            attempt = session.get(Attempt, attempt_id)
            self.assertEqual(attempt.status, 'submitted')
            self.assertEqual(attempt.submitted_at, attempt.deadline)
