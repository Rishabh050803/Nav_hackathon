from fastapi import APIRouter, Depends

from app.auth import current_user
from app.models import utc_now
from app.conduction import service
from app.conduction.schemas import AnswerRequest, AttemptView, ReviewRequest, Versioned
from app.conduction.transactions import transaction

router = APIRouter(prefix='/api', tags=['Exam conduction'])


@router.post('/exams/{exam_id}/attempt', response_model=AttemptView)
def start_attempt(exam_id: int, user_id: int = Depends(current_user)):
    with transaction() as session:
        now = utc_now()
        attempt = service.start(session, exam_id, user_id, now)
        return service.view(attempt, now)


@router.get('/attempts/{attempt_id}', response_model=AttemptView)
def get_attempt(attempt_id: int, user_id: int = Depends(current_user)):
    with transaction() as session:
        now = utc_now()
        return service.view(service.owned(session, attempt_id, user_id, now), now)


def change(attempt_id, user_id, action, body, question_id=None, **kwargs):
    with transaction() as session:
        now = utc_now()
        attempt = service.owned(session, attempt_id, user_id, now)
        service.mutate(attempt, action, body.version, now, question_id=question_id, **kwargs)
        return service.view(attempt, now)


@router.put('/attempts/{attempt_id}/questions/{question_id}/answer', response_model=AttemptView)
def save_answer(attempt_id: int, question_id: int, body: AnswerRequest, user_id: int = Depends(current_user)):
    return change(attempt_id, user_id, 'answer', body, question_id, option_id=body.option_id)


@router.post('/attempts/{attempt_id}/questions/{question_id}/visit', response_model=AttemptView)
def visit_question(attempt_id: int, question_id: int, body: Versioned, user_id: int = Depends(current_user)):
    return change(attempt_id, user_id, 'navigate', body, question_id)


@router.put('/attempts/{attempt_id}/questions/{question_id}/review', response_model=AttemptView)
def mark_review(attempt_id: int, question_id: int, body: ReviewRequest, user_id: int = Depends(current_user)):
    return change(attempt_id, user_id, 'review', body, question_id, marked=body.marked)


@router.post('/attempts/{attempt_id}/pause', response_model=AttemptView)
def pause(attempt_id: int, body: Versioned, user_id: int = Depends(current_user)):
    return change(attempt_id, user_id, 'pause', body)


@router.post('/attempts/{attempt_id}/resume', response_model=AttemptView)
def resume(attempt_id: int, body: Versioned, user_id: int = Depends(current_user)):
    return change(attempt_id, user_id, 'resume', body)


@router.post('/attempts/{attempt_id}/submit', response_model=AttemptView)
def submit(attempt_id: int, body: Versioned, user_id: int = Depends(current_user)):
    return change(attempt_id, user_id, 'submit', body)


@router.post('/attempts/{attempt_id}/heartbeat', response_model=AttemptView)
def heartbeat(attempt_id: int, body: Versioned, user_id: int = Depends(current_user)):
    return change(attempt_id, user_id, 'heartbeat', body)
