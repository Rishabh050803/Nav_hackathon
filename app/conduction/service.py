"""Server-authoritative exam state machine; callers serialize SQLite writes.

All times are naive UTC internally. Focus time means wall time on the active
question, excluding pauses, capped at the exam deadline. It is not proof of attention.
"""
from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exam_analysis.service import build_report
from app.models import Exam
from app.conduction.models import Attempt, AttemptEvent, AttemptQuestion

PAUSE_LIMIT = timedelta(minutes=15)


class RuleError(Exception):
    def __init__(self, message: str, status: int = 409):
        self.message, self.status = message, status
        super().__init__(message)


def record(attempt: Attempt, kind: str, now: datetime, question_id=None, **details):
    attempt.events.append(AttemptEvent(kind=kind, occurred_at=now, question_id=question_id, details=details))


def question(attempt: Attempt, question_id: int) -> AttemptQuestion:
    for item in attempt.questions:
        if item.id == question_id:
            return item
    raise RuleError('Question does not belong to this attempt', 404)


def accrue(attempt: Attempt, now: datetime):
    if attempt.status == 'running' and attempt.active_question_id and attempt.focus_started_at:
        end = min(now, attempt.deadline)
        item = question(attempt, attempt.active_question_id)
        item.time_spent_ms += max(0, int((end - attempt.focus_started_at).total_seconds() * 1000))
        attempt.focus_started_at = end


def finish(attempt: Attempt, now: datetime, reason: str):
    accrue(attempt, now)
    attempt.status = 'submitted'
    attempt.submitted_at = now
    attempt.submission_reason = reason
    attempt.focus_started_at = None
    attempt.version += 1
    record(attempt, 'submitted', now, reason=reason)


def settle(attempt: Attempt, now: datetime) -> bool:
    if attempt.status == 'running' and now >= attempt.deadline:
        finish(attempt, attempt.deadline, 'time_expired')
        return True
    if attempt.status == 'paused' and now >= attempt.pause_expires_at:
        finish(attempt, attempt.pause_expires_at, 'pause_expired')
        return True
    return False


def owned(session: Session, attempt_id: int, user_id: int, now: datetime) -> Attempt:
    attempt = session.scalar(select(Attempt).where(Attempt.id == attempt_id, Attempt.user_id == user_id))
    if attempt is None:
        raise RuleError('Attempt not found', 404)
    settle(attempt, now)
    return attempt


def snapshot_questions(exam: Exam) -> list[AttemptQuestion]:
    if exam.paper is not None:
        return [
            AttemptQuestion(
                # Generated questions have a negative, paper-local source ID.
                source_question_id=item.get('bank_question_id', -item['position']),
                position=item['position'], question_text=item['question_text'],
                topic=item['topic'], difficulty=item['difficulty'], has_image=False,
                options=[dict(option, id=index) for index, option in enumerate(item['options'], start=1)],
            )
            for item in exam.paper['questions']
        ]

    # Legacy join table has no order, so freeze deterministic ID order at start.
    return [
        AttemptQuestion(
            source_question_id=question.id, position=index, question_text=question.question_text,
            topic=question.topic, difficulty=question.difficulty, has_image=question.has_image,
            options=[dict(id=option.id, label=option.label, text=option.text, is_correct=option.is_correct)
                     for option in sorted(question.options, key=lambda option: option.id)],
        )
        for index, question in enumerate(sorted(exam.questions, key=lambda question: question.id), start=1)
    ]

def start(session: Session, exam_id: int, user_id: int, now: datetime) -> Attempt:
    exam = session.scalar(select(Exam).where(Exam.id == exam_id, Exam.user_id == user_id))
    if exam is None:
        raise RuleError('Exam not found', 404)
    existing = session.scalar(select(Attempt).where(Attempt.exam_id == exam_id, Attempt.user_id == user_id))
    if existing:
        settle(existing, now)
        return existing  # Retried start never resets an attempt.
    duration = exam.total_time_seconds or exam.max_time * 60
    questions = snapshot_questions(exam)
    if duration <= 0 or not questions:
        raise RuleError('Exam must contain questions and have a positive time limit', 422)
    if any(not question.options for question in questions):
        raise RuleError('Every question must have at least one option', 422)
    attempt = Attempt(exam_id=exam.id, user_id=user_id, title=exam.title,
                      duration_seconds=duration, started_at=now,
                      deadline=now + timedelta(seconds=duration), status='running', version=1)
    attempt.questions = questions
    session.add(attempt)
    session.flush()
    first = attempt.questions[0]
    attempt.active_question_id = first.id
    attempt.focus_started_at = now
    first.visit_count, first.first_visited_at = 1, now
    record(attempt, 'started', now)
    record(attempt, 'visited', now, first.id)
    return attempt


def mutate(attempt: Attempt, action: str, version: int, now: datetime,
           question_id=None, option_id=None, marked=None):
    # Repeated submit is harmless, including after automatic submission.
    if action == 'submit' and attempt.status == 'submitted':
        return
    if attempt.version != version:
        raise RuleError('Attempt changed; reload state and retry with its current version')
    if attempt.status == 'submitted':
        raise RuleError('Exam has already been submitted')
    if action == 'resume':
        if attempt.status != 'paused':
            raise RuleError('Only a paused exam can be resumed')
        attempt.deadline += now - attempt.paused_at
        attempt.paused_at = attempt.pause_expires_at = None
        attempt.status = 'running'
        attempt.focus_started_at = now
        record(attempt, 'resumed', now)
    elif action == 'submit':
        finish(attempt, now, 'manual')
        return
    else:
        if attempt.status != 'running':
            raise RuleError('Resume the exam before making changes')
        item = question(attempt, question_id) if question_id is not None else None
        if action in ('navigate', 'answer', 'review') and item is None:
            raise RuleError('Question is required', 422)
        if action == 'answer':
            if option_id is not None and not any(o['id'] == option_id for o in item.options):
                raise RuleError('Option does not belong to this question', 422)
            previous = item.selected_option_id
            if previous != option_id:
                item.selected_option_id = option_id
                item.answer_change_count += 1
                item.answered_at = now if option_id is not None else None
                record(attempt, 'answer_saved', now, item.id, previous_option_id=previous, option_id=option_id)
        elif action == 'navigate':
            if attempt.active_question_id != item.id:
                accrue(attempt, now)
                attempt.active_question_id, attempt.focus_started_at = item.id, now
                item.visit_count += 1
                item.first_visited_at = item.first_visited_at or now
                record(attempt, 'visited', now, item.id)
        elif action == 'review':
            item.marked_for_review = marked
            record(attempt, 'review_changed', now, item.id, marked=marked)
        elif action == 'pause':
            accrue(attempt, now)
            attempt.status = 'paused'
            attempt.paused_at, attempt.pause_expires_at = now, now + PAUSE_LIMIT
            attempt.focus_started_at = None
            record(attempt, 'paused', now)
        elif action == 'heartbeat':
            accrue(attempt, now)
        else:
            raise RuleError('Unknown action', 422)
    attempt.version += 1


def view(attempt: Attempt, now: datetime) -> dict:
    remaining = 0
    if attempt.status != 'submitted':
        reference = attempt.paused_at if attempt.status == 'paused' else now
        remaining = max(0, ceil((attempt.deadline - reference).total_seconds()))
    questions = []
    for item in attempt.questions:
        elapsed = item.time_spent_ms
        if attempt.status == 'running' and attempt.active_question_id == item.id and attempt.focus_started_at:
            elapsed += max(0, int((min(now, attempt.deadline) - attempt.focus_started_at).total_seconds() * 1000))
        questions.append(dict(
            id=item.id, source_question_id=item.source_question_id, position=item.position,
            question_text=item.question_text, topic=item.topic, difficulty=item.difficulty,
            has_image=item.has_image,
            options=[{k: o[k] for k in ('id', 'label', 'text')} for o in item.options],
            selected_option_id=item.selected_option_id, marked_for_review=item.marked_for_review,
            time_spent_ms=elapsed, visit_count=item.visit_count,
            answer_change_count=item.answer_change_count,
        ))
    return dict(id=attempt.id, exam_id=attempt.exam_id, title=attempt.title, status=attempt.status,
                version=attempt.version, server_time=now, started_at=attempt.started_at,
                deadline=attempt.deadline if attempt.status == 'running' else None,
                pause_expires_at=attempt.pause_expires_at if attempt.status == 'paused' else None,
                remaining_seconds=remaining, duration_seconds=attempt.duration_seconds,
                submitted_at=attempt.submitted_at, submission_reason=attempt.submission_reason,
                active_question_id=attempt.active_question_id,
                answered_count=sum(q.selected_option_id is not None for q in attempt.questions),
                questions=questions)


def require_submitted(attempt: Attempt):
    if attempt.status != 'submitted':
        raise RuleError('Analysis is available after submission')


def analysis(attempt: Attempt) -> dict:
    require_submitted(attempt)
    return build_report(attempt)
