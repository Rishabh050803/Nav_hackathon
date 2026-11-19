from fastapi import APIRouter, Depends

from app.auth import current_user
from app.conduction.service import analysis, owned
from app.conduction.transactions import transaction
from app.exam_analysis.schemas import AnalysisView, PerformanceView, SuggestionsView
from app.exam_analysis.history import build_history, load_history
from app.exam_analysis.coaching import suggestions
from app.models import utc_now

router = APIRouter(prefix='/api/attempts', tags=['Exam analysis'])


@router.get('/{attempt_id}/analysis', response_model=AnalysisView)
def get_analysis(attempt_id: int, user_id: int = Depends(current_user)):
    with transaction() as session:
        attempt = owned(session, attempt_id, user_id, utc_now())
        session.flush()  # Include IDs of any auto-submission event.
        return analysis(attempt)


history_router = APIRouter(prefix='/api/analysis', tags=['Exam analysis'])


def performance_for(user_id):
    with transaction() as session:
        now = utc_now()
        attempts = load_history(session, user_id, now)
        # Detach a complete snapshot before releasing the database transaction.
        performance = build_history(attempts, now)
    return performance


@history_router.get('/performance', response_model=PerformanceView)
def get_performance(user_id: int = Depends(current_user)):
    """Submitted exam history and deterministic performance statistics."""
    return performance_for(user_id)


@history_router.post('/suggestions', response_model=SuggestionsView)
def get_suggestions(user_id: int = Depends(current_user)):
    """Request AI coaching; return labelled rule-based advice on model failure."""
    # No SQLite transaction is held during the external model call.
    return suggestions(performance_for(user_id))
