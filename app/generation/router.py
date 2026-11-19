"""Exam generation HTTP routes; business logic lives in the service."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.generation import service
from app.generation.dependencies import caller_id, get_session
from app.generation.schemas import ExamPaper, GenerateExam

router = APIRouter(prefix="/api/exams", tags=["Exam generation"])


@router.post("/generate", status_code=201, response_model=ExamPaper)
def generate_exam(
    request: GenerateExam,
    user_id: Annotated[int, Depends(caller_id)],
    session: Annotated[Session, Depends(get_session)],
):
    return service.generate_exam(session, user_id, request)


@router.get("/{exam_id}", response_model=ExamPaper)
def get_exam(
    exam_id: int,
    user_id: Annotated[int, Depends(caller_id)],
    session: Annotated[Session, Depends(get_session)],
):
    return service.get_exam(session, user_id, exam_id)
