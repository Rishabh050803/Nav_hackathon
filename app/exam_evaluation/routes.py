from fastapi import APIRouter, Depends

from app.auth import current_user
from app.conduction.service import owned, require_submitted
from app.conduction.transactions import transaction
from app.exam_evaluation.schemas import EvaluationView
from app.exam_evaluation.service import grade, summarize
from app.models import utc_now

router = APIRouter(prefix='/api/attempts', tags=['Exam evaluation'])


@router.get('/{attempt_id}/evaluation', response_model=EvaluationView)
def get_evaluation(attempt_id: int, user_id: int = Depends(current_user)):
    with transaction() as session:
        attempt = owned(session, attempt_id, user_id, utc_now())
        require_submitted(attempt)
        return dict(attempt_id=attempt.id, **summarize([grade(question) for question in attempt.questions]))
