from fastapi import FastAPI

from app.exams import router

app = FastAPI(title="PrepX", description="Exam creation backend", version="0.2.0")
app.include_router(router)
