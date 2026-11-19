import logging
import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import current_user
from app.database import SessionLocal
from .config import get_settings
from .errors import ServiceError
from .languages import LANGUAGES
from .ocr import LocalOCR
from .pdf import inspect_pdf
from .schemas import BatchResult
from .service import IngestionService

router = APIRouter(prefix='/api/v1', tags=['PDF ingestion'])
log = logging.getLogger(__name__)
ingestion_lock = threading.Lock()


def get_session():
    with SessionLocal() as session:
        yield session


def get_service():
    return IngestionService(get_settings())


@router.get('/languages')
def languages(user_id: Annotated[int, Depends(current_user)]):
    return {'languages': LocalOCR(get_settings()).capabilities(),
            'note': 'EasyOCR availability describes library models, not locally installed weights.'}


@router.post('/ingestions', response_model=BatchResult)
def ingest(
    files: Annotated[list[UploadFile], File(description='PDFs with 1-10 pages each')],
    user_id: Annotated[int, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[IngestionService, Depends(get_service)],
    exam_id: Annotated[int | None, Form(gt=0)] = None,
    examname: Annotated[str | None, Form(max_length=255)] = None,
    languages: Annotated[str | None, Form(description='Codes such as en,hi; omit for installed Tesseract models')] = None,
    ocr_backend: Annotated[Literal['tesseract', 'easyocr'], Form()] = 'tesseract',
    force_ocr: Annotated[bool, Form()] = False,
):
    settings = get_settings()
    if len(files) > settings.max_files:
        raise ServiceError(f'Maximum {settings.max_files} PDFs per request.', 413)
    hints = list(dict.fromkeys(c.strip() for c in languages.split(',') if c.strip())) if languages else None
    if languages is not None and not hints:
        raise ServiceError('Provide a language code or omit languages.')
    if hints and any(c not in LANGUAGES for c in hints):
        raise ServiceError('Only Indian-language and English codes are allowed. See /api/v1/languages.')
    if not ingestion_lock.acquire(blocking=False):
        raise ServiceError('Another PDF batch is processing; retry later.', 429)
    try:
        uploads = []
        for file in files:
            if not file.filename or not file.filename.lower().endswith('.pdf'):
                raise ServiceError('Only .pdf files are accepted.')
            data = file.file.read(settings.max_file_mb * 1024 * 1024 + 1)
            if len(data) > settings.max_file_mb * 1024 * 1024:
                raise ServiceError(f'Each PDF must be at most {settings.max_file_mb} MB.', 413)
            inspect_pdf(data, settings)
            uploads.append((file.filename, data))
        try:
            return service.ingest(session, uploads, user_id, exam_id, examname, hints, ocr_backend, force_ocr)
        except SQLAlchemyError as exc:
            log.error('Ingestion database operation failed', exc_info=True)
            raise ServiceError('Database operation failed. Check main backend migrations; nothing was saved.', 503) from exc
    finally:
        ingestion_lock.release()
        for file in files:
            file.file.close()


async def service_error(_, exc):
    return JSONResponse(status_code=exc.status, content={'detail': str(exc)})


def install(app: FastAPI):
    """Explicit integration hook, also used by the no-parent-edit entrypoint."""
    if getattr(app.state, 'prepx_ingestion_installed', False):
        return
    app.include_router(router)
    app.add_exception_handler(ServiceError, service_error)
    app.state.prepx_ingestion_installed = True
