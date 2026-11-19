"""Run all conduction routes against a real Uvicorn server and temporary SQLite DB.

Run: .venv/bin/python tests/live_endpoints.py
No existing database, credentials, or running server is used.
"""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix='prepx-live-') as directory:
        os.environ['DATABASE_URL'] = f'sqlite:///{directory}/live.db'
        os.environ['AUTH_TOKEN_SECRET'] = secrets.token_hex(32)
        # Live smoke tests use deterministic fallback, never a paid model call.
        for key in ('ANALYSIS_LLM_URL', 'ANALYSIS_LLM_MODEL', 'ANALYSIS_LLM_API_KEY',
                    'EXAM_LLM_URL', 'EXAM_LLM_MODEL', 'EXAM_LLM_API_KEY'):
            os.environ[key] = ''
        from alembic import command
        from alembic.config import Config
        import httpx
        from app.auth import mint_token
        from app.database import SessionLocal, engine
        from app.models import Exam, Option, Question, User, utc_now
        from app.conduction.models import Attempt

        command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
        with SessionLocal() as session:
            owner = User(first_name='Live', username='live-owner')
            stranger = User(first_name='Other', username='live-other')
            questions = [Question(question_text=f'Question {i}', options=[
                Option(label='A', text='Correct', is_correct=True),
                Option(label='B', text='Incorrect', is_correct=False),
            ]) for i in range(2)]
            exams = [Exam(user=owner, title=name, total_time_seconds=duration,
                          questions=questions, question_count=2)
                     for name, duration in [('Normal', 120), ('Timeout', 2), ('Paused', 120)]]
            session.add_all(exams + [stranger])
            session.commit()
            exam_ids = [exam.id for exam in exams]
            token, other_token = mint_token(owner.id), mint_token(stranger.id)

        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        base = f'http://127.0.0.1:{listener.getsockname()[1]}'
        log = open(Path(directory) / 'server.log', 'w+')
        process = None
        checks = 0
        client = httpx.Client(base_url=base, headers={'Authorization': f'Bearer {token}'}, timeout=5)

        def start_server():
            nonlocal process
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'app.main:app', '--fd', str(listener.fileno()),
                 '--log-level', 'warning'], cwd=ROOT, env=os.environ.copy(),
                pass_fds=(listener.fileno(),), stdout=log, stderr=log)
            for _ in range(100):
                if process.poll() is not None:
                    log.flush()
                    log.seek(0)
                    raise AssertionError(f'Server exited: {log.read()}')
                try:
                    if client.get('/openapi.json', timeout=0.2).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            raise AssertionError('Server did not start')

        def stop_server():
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        def request(method, path, expected=200, **kwargs):
            nonlocal checks
            result = client.request(method, path, **kwargs)
            assert result.status_code == expected, (method, path, result.status_code, result.text)
            checks += 1
            return result.json()

        def act(state, suffix, method='POST', **data):
            return request(method, f"/api/attempts/{state['id']}/{suffix}",
                           json={'version': state['version'], **data})

        def wait_for_submission(attempt_id):
            # Inspect SQLite, without any HTTP attempt request that could itself settle expiry.
            limit = time.monotonic() + 6
            while time.monotonic() < limit:
                with SessionLocal() as session:
                    if session.get(Attempt, attempt_id).status == 'submitted':
                        return
                time.sleep(0.05)
            raise AssertionError('Background worker did not submit the attempt')

        try:
            start_server()
            s = request('POST', f'/api/exams/{exam_ids[0]}/attempt')
            aid = s['id']
            assert s['status'] == 'running' and s['deadline'].endswith('Z')
            assert 'is_correct' not in str(s)
            assert request('POST', f'/api/exams/{exam_ids[0]}/attempt')['id'] == aid
            request('GET', f'/api/attempts/{aid}')
            request('GET', f'/api/attempts/{aid}/analysis', 409)
            request('GET', f'/api/attempts/{aid}', 401, headers={'Authorization': 'Bearer invalid'})
            request('GET', f'/api/attempts/{aid}', 404,
                    headers={'Authorization': f'Bearer {other_token}'})
            q1, q2 = s['questions']
            path = f"questions/{q1['id']}/answer"
            request('PUT', f'/api/attempts/{aid}/{path}', 422, json={'version': s['version']})
            request('PUT', f'/api/attempts/{aid}/{path}', 422,
                    json={'version': s['version'], 'option_id': q2['options'][0]['id']})
            old_version = s['version']
            s = act(s, path, 'PUT', option_id=q1['options'][0]['id'])
            assert s['answered_count'] == 1
            request('PUT', f'/api/attempts/{aid}/{path}', 409,
                    json={'version': old_version, 'option_id': None})
            s = act(s, path, 'PUT', option_id=q1['options'][1]['id'])
            assert s['questions'][0]['selected_option_id'] == q1['options'][1]['id']
            s = act(s, path, 'PUT', option_id=None)
            assert s['answered_count'] == 0
            s = act(s, path, 'PUT', option_id=q1['options'][0]['id'])
            time.sleep(0.05)
            s = act(s, f"questions/{q2['id']}/visit")
            assert s['active_question_id'] == q2['id']
            s = act(s, f"questions/{q2['id']}/review", 'PUT', marked=True)
            assert s['questions'][1]['marked_for_review']
            s = act(s, 'heartbeat')
            s = act(s, 'pause')
            assert s['status'] == 'paused' and s['pause_expires_at'].endswith('Z')
            request('PUT', f'/api/attempts/{aid}/{path}', 409,
                    json={'version': s['version'], 'option_id': None})
            s = act(s, 'resume')
            assert s['status'] == 'running'
            stop_server()
            start_server()
            recovered = request('GET', f'/api/attempts/{aid}')
            assert recovered['version'] == s['version']
            assert recovered['questions'][0]['selected_option_id'] == q1['options'][0]['id']
            s = act(recovered, 'submit')
            assert s['status'] == 'submitted' and s['submission_reason'] == 'manual'
            assert act(s, 'submit')['version'] == s['version']
            request('PUT', f'/api/attempts/{aid}/{path}', 409,
                    json={'version': s['version'], 'option_id': None})
            report = request('GET', f'/api/attempts/{aid}/analysis')
            assert report['correct_count'] == 1 and report['answered_count'] == 1
            assert all(q['time_spent_ms'] > 0 for q in report['questions'])
            assert report['events'][-1]['occurred_at'].endswith('Z')
            print('PASS: all 10 endpoint operations; answer changes/clear, navigation, review, pause/resume, analysis')
            print('PASS: authentication, ownership, validation, stale writes, final submission, restart persistence')

            timed = request('POST', f'/api/exams/{exam_ids[1]}/attempt')
            wait_for_submission(timed['id'])
            timed = request('GET', f"/api/attempts/{timed['id']}")
            assert timed['submission_reason'] == 'time_expired'
            assert timed['questions'][0]['time_spent_ms'] == 2000
            print('PASS: real 2-second deadline auto-submits without browser requests')

            paused = request('POST', f'/api/exams/{exam_ids[2]}/attempt')
            paused = act(paused, 'pause')
            # Advance only this test fixture's pause deadline; no 15-minute wall-clock wait.
            with SessionLocal() as session:
                row = session.get(Attempt, paused['id'])
                row.paused_at = utc_now() - timedelta(minutes=15, seconds=1)
                row.pause_expires_at = row.paused_at + timedelta(minutes=15)
                session.commit()
            wait_for_submission(paused['id'])
            paused = request('GET', f"/api/attempts/{paused['id']}")
            assert paused['submission_reason'] == 'pause_expired'
            request('POST', f"/api/attempts/{paused['id']}/resume", 409,
                    json={'version': paused['version']})
            print('PASS: expired pause auto-submits and prevents resume (deadline advanced in fixture)')
            performance = request('GET', '/api/analysis/performance')
            assert performance['submitted_exam_count'] == 3
            assert performance['overall']['total_questions'] == 6
            assert performance['overall']['correct_count'] == 1
            assert performance['overall']['score_percent'] == 16.67
            other_history = request('GET', '/api/analysis/performance',
                                    headers={'Authorization': f'Bearer {other_token}'})
            assert other_history['submitted_exam_count'] == 0
            request('GET', '/api/analysis/performance', 401,
                    headers={'Authorization': 'Bearer invalid'})
            coaching = request('POST', '/api/analysis/suggestions')
            assert coaching['source'] == 'rules' and coaching['fallback_reason'] == 'not_configured'
            assert coaching['advice']['recommendations']
            print('PASS: history statistics, coaching fallback, authentication, and history isolation')
            print(f'PASS: {checks} real HTTP response checks; temporary database only')
        finally:
            stop_server()
            client.close()
            listener.close()
            log.close()
            engine.dispose()


if __name__ == '__main__':
    main()
