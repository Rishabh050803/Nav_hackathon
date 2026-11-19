import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.conduction.router import router as conduction_router
from app.generation.router import router as generation_router
from app.conduction.service import RuleError
from app.conduction.worker import run


@asynccontextmanager
async def lifespan(app):
    worker = asyncio.create_task(run())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title='PrepX', description='Exam creation and conduction backend',
              version='0.2.0', lifespan=lifespan)
app.include_router(generation_router)
app.include_router(conduction_router)


@app.exception_handler(RuleError)
async def exam_rule_error(request: Request, exc: RuleError):
    return JSONResponse(status_code=exc.status, content={'detail': exc.message})
