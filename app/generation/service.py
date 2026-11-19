"""Question selection, model generation, and exam paper persistence."""
from collections import Counter
from random import shuffle

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.generation.llm import GenerationUnavailable, InvalidGeneration, generate_questions
from app.generation.schemas import ExamPaper, GenerateExam, GeneratedQuestion
from app.generation.utils import Difficulties, normalize
from app.models import Exam, PdfUpload, Question, User


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



def generate_exam(session: Session, user_id: int, request: GenerateExam) -> ExamPaper:
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


def get_exam(session: Session, user_id: int, exam_id: int) -> ExamPaper:
    exam = session.scalar(select(Exam).where(Exam.id == exam_id, Exam.user_id == user_id))
    if exam is None or exam.paper is None:
        raise HTTPException(404, "Generated paper not found.")
    return public_paper(exam)
