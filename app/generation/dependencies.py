"""Generation API access and database session dependencies."""
from hmac import compare_digest
import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import bearer, current_user

from app.database import SessionLocal


def get_session():
    with SessionLocal() as session:
        yield session


def caller_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    authorization: Annotated[str | None, Header()] = None,
    x_exam_api_key: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[int | None, Header(gt=0)] = None,
) -> int:
    if authorization is not None:
        return current_user(credentials)
    if x_exam_api_key is None:
        return current_user(None)
    expected = os.getenv("EXAM_API_KEY")
    if not expected:
        raise HTTPException(503, "Exam API access is not configured.")
    if not x_exam_api_key or not compare_digest(expected.encode(), x_exam_api_key.encode()):
        raise HTTPException(401, "Invalid exam API key.")
    if x_user_id is None:
        raise HTTPException(400, "X-User-Id is required.")
    return x_user_id

