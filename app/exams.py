"""Select questions, generate variants, and persist complete exam papers."""

from collections import Counter
from datetime import datetime
from hmac import compare_digest
import os
from random import shuffle
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.exam_llm import GenerationUnavailable, InvalidGeneration, generate_questions
from app.models import Exam, PdfUpload, Question, User

router = APIRouter(prefix="/api/exams", tags=["exams"])
Difficulty = Literal["easy", "medium", "hard"]
Difficulties = ("easy", "medium", "hard")
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class DifficultyCounts(BaseModel, extra="forbid"):
    easy: int = Field(default=0, ge=0, strict=True)
    medium: int = Field(default=0, ge=0, strict=True)
    hard: int = Field(default=0, ge=0, strict=True)


class GenerateExam(BaseModel, extra="forbid"):
    title: Topic = "Practice exam"
    question_count: int = Field(ge=1, le=100, strict=True)
    per_question_time_seconds: int = Field(ge=1, le=3600, strict=True)
    difficulty: Difficulty | None = None
    difficulty_counts: DifficultyCounts | None = None
    important_topics: list[Topic] = Field(default_factory=list, max_length=20)
    generated_question_count: int = Field(default=0, ge=0, le=20, strict=True)
    pdf_upload_id: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def validate_counts(self):
        if (self.difficulty is None) == (self.difficulty_counts is None):
            raise ValueError("Provide either difficulty or difficulty_counts.")
        if sum(self.counts().values()) != self.question_count:
            raise ValueError("Difficulty counts must sum to question_count.")
        if self.generated_question_count > self.question_count:
            raise ValueError("generated_question_count cannot exceed question_count.")
        if len({normalize(topic) for topic in self.important_topics}) != len(self.important_topics):
            raise ValueError("Important topics must be unique.")
        return self

    def counts(self) -> dict[str, int]:
        if self.difficulty_counts:
            return self.difficulty_counts.model_dump()
        return {level: self.question_count if self.difficulty == level else 0
                for level in Difficulties}


class GeneratedQuestion(BaseModel, extra="forbid"):
    question_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    options: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]] = Field(
        min_length=4, max_length=4
    )
    correct_index: int = Field(ge=0, le=3, strict=True)


class PaperOption(BaseModel):
    label: str
    text: str


class PaperQuestion(BaseModel):
    position: int
    question_text: str
    difficulty: Difficulty
    topic: str | None
    source: Literal["previous_year", "generated"]
    options: list[PaperOption]


class ExamPaper(BaseModel):
    id: int
    title: str
    created_at: datetime
    question_count: int
    per_question_time_seconds: int
    total_time_seconds: int
    difficulty_counts: DifficultyCounts
    important_topics: list[str]
    generated_question_count: int
    pdf_upload_id: int | None
    questions: list[PaperQuestion]


def normalize(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def get_session():
    with SessionLocal() as session:
        yield session


def caller_id(
    x_exam_api_key: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[int | None, Header(gt=0)] = None,
) -> int:
    expected = os.getenv("EXAM_API_KEY")
    if not expected:
        raise HTTPException(503, "Exam API access is not configured.")
    if not x_exam_api_key or not compare_digest(expected.encode(), x_exam_api_key.encode()):
        raise HTTPException(401, "Invalid exam API key.")
    if x_user_id is None:
        raise HTTPException(400, "X-User-Id is required.")
    return x_user_id


def load_bank(session: Session, user_id: int, upload_id: int | None) -> list[Question]:
    statement = (
        select(Question)
        .outerjoin(PdfUpload)
        .options(selectinload(Question.options))
        .where(
            or_(Question.pdf_upload_id.is_(None), PdfUpload.user_id == user_id),
            Question.has_image.is_not(True),
        )
    )
    if upload_id is not None:
        if session.scalar(select(PdfUpload.id).where(PdfUpload.id == upload_id, PdfUpload.user_id == user_id)) is None:
            raise HTTPException(404, "PDF upload not found.")
        statement = statement.where(Question.pdf_upload_id == upload_id)

    questions = []
    for question in session.scalars(statement):
        options = question.options
        labels = {normalize(option.label) for option in options}
        texts = {normalize(option.text) for option in options}
        if (
            normalize(question.question_text)
            and normalize(question.difficulty) in Difficulties
            and len(options) >= 2
            and len(options) == len(labels) == len(texts)
            and "" not in labels and "" not in texts
            and all(option.is_correct is not None for option in options)
            and sum(option.is_correct is True for option in options) == 1
        ):
            questions.append(question)
    shuffle(questions)
    return questions


def select_bank_questions(bank, counts, bank_count, priorities):
    remaining = counts.copy()
    used_texts = set()
    topic_counts = Counter()
    selected = []
    # Fill the most constrained difficulty first within each priority pass.
    for focused in (True, False):
        for level in sorted(counts, key=lambda level: sum(normalize(q.difficulty) == level for q in bank)):
            candidates = [
                q for q in bank if normalize(q.difficulty) == level
                and (normalize(q.topic) in priorities) == focused
            ]
            while candidates and remaining[level] and len(selected) < bank_count:
                question = min(candidates, key=lambda q: topic_counts[normalize(q.topic)]) if focused else candidates[0]
                candidates.remove(question)
                text = normalize(question.question_text)
                if text in used_texts:
                    continue
                used_texts.add(text)
                topic_counts[normalize(question.topic)] += 1
                selected.append(question)
                remaining[level] -= 1
    if len(selected) != bank_count:
        raise HTTPException(422, "Too few eligible bank questions for the requested difficulty mix and bank count.")
    return selected, remaining, topic_counts


def bank_item(question: Question) -> dict:
    return {
        "source": "previous_year",
        "question_text": question.question_text,
        "difficulty": normalize(question.difficulty),
        "topic": question.topic,
        "bank_question_id": question.id,
        "options": [
            {"label": option.label, "text": option.text, "is_correct": option.is_correct}
            for option in sorted(question.options, key=lambda option: (option.label, option.id))
        ],
    }


def generated_items(bank, remaining, priorities, topic_counts):
    if not any(remaining.values()):
        return []
    if not bank:
        raise HTTPException(422, "At least one eligible previous-year question is required for generation.")

    slots = []
    for difficulty, count in remaining.items():
        for _ in range(count):
            focus = min(priorities, key=topic_counts.__getitem__) if priorities else None
            references = sorted(
                bank,
                key=lambda q: (focus is not None and normalize(q.topic) != focus,
                               normalize(q.difficulty) != difficulty),
            )
            reference = references[0]
            topic = priorities[focus] if focus is not None else reference.topic
            topic_counts[normalize(topic)] += 1
            slots.append({
                "difficulty": difficulty,
                "topic": topic,
                "references": [
                    {"question_text": q.question_text[:1500], "topic": q.topic, "difficulty": q.difficulty}
                    for q in references[:2]
                ],
            })

    seen = {normalize(q.question_text) for q in bank}
    items = []
    try:
        for slot, raw in zip(slots, generate_questions(slots)):
            question = GeneratedQuestion.model_validate(raw)
            text = normalize(question.question_text)
            if text in seen or len({normalize(option) for option in question.options}) != 4:
                raise InvalidGeneration
            seen.add(text)
            items.append({
                "source": "generated",
                "difficulty": slot["difficulty"],
                "topic": slot["topic"],
                "question_text": question.question_text,
                "options": [
                    {"label": label, "text": option, "is_correct": index == question.correct_index}
                    for index, (label, option) in enumerate(zip("ABCD", question.options))
                ],
            })
    except GenerationUnavailable as error:
        raise HTTPException(503, "The exam question model is unavailable.") from error
    except (ValidationError, InvalidGeneration) as error:
        raise HTTPException(502, "The exam question model returned an invalid paper.") from error
    return items


def public_paper(exam: Exam) -> ExamPaper:
    return ExamPaper(
        id=exam.id, title=exam.title, created_at=exam.created_at,
        question_count=exam.question_count,
        per_question_time_seconds=exam.per_question_time_seconds,
        total_time_seconds=exam.total_time_seconds, pdf_upload_id=exam.pdf_upload_id,
        **exam.paper,
    )


@router.post("/generate", status_code=201, response_model=ExamPaper)
def generate_exam(
    request: GenerateExam,
    user_id: Annotated[int, Depends(caller_id)],
    session: Annotated[Session, Depends(get_session)],
):
    if session.get(User, user_id) is None:
        raise HTTPException(404, "User not found.")
    counts = request.counts()
    priorities = {normalize(topic): topic for topic in request.important_topics}
    bank = load_bank(session, user_id, request.pdf_upload_id)
    selected, remaining, topic_counts = select_bank_questions(
        bank, counts, request.question_count - request.generated_question_count, priorities,
    )
    questions = [bank_item(question) for question in selected]
    # Release the read transaction before waiting on the external service.
    session.expunge_all()
    session.rollback()
    questions.extend(generated_items(bank, remaining, priorities, topic_counts))
    shuffle(questions)
    for position, question in enumerate(questions, start=1):
        question["position"] = position

    total_seconds = request.question_count * request.per_question_time_seconds
    exam = Exam(
        user_id=user_id, title=request.title, pdf_upload_id=request.pdf_upload_id,
        question_count=request.question_count,
        per_question_time_seconds=request.per_question_time_seconds,
        total_time_seconds=total_seconds, max_time=(total_seconds + 59) // 60,
        important_topics=", ".join(request.important_topics) or None,
        questions=selected,
        paper={
            "difficulty_counts": counts, "important_topics": request.important_topics,
            "generated_question_count": request.generated_question_count, "questions": questions,
        },
    )
    session.add(exam)
    session.flush()
    result = public_paper(exam)
    session.commit()
    return result


@router.get("/{exam_id}", response_model=ExamPaper)
def get_exam(
    exam_id: int,
    user_id: Annotated[int, Depends(caller_id)],
    session: Annotated[Session, Depends(get_session)],
):
    exam = session.scalar(select(Exam).where(Exam.id == exam_id, Exam.user_id == user_id))
    if exam is None or exam.paper is None:
        raise HTTPException(404, "Generated paper not found.")
    return public_paper(exam)
