from fastapi import APIRouter, Depends

from app.auth import current_user
from app.conduction.service import analysis, owned
from app.conduction.transactions import transaction
from app.exam_analysis.schemas import AnalysisView
from app.models import utc_now

router = APIRouter(prefix='/api/attempts', tags=['Exam analysis'])


@router.get('/{attempt_id}/analysis', response_model=AnalysisView)
def get_analysis(attempt_id: int, user_id: int = Depends(current_user)):
    with transaction() as session:
        attempt = owned(session, attempt_id, user_id, utc_now())
        session.flush()  # Include IDs of any auto-submission event.
        return analysis(attempt)
