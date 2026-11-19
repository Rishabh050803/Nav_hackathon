"""Database entities mapped from the original PrepX Java backend.

Timestamps use naive UTC. Nullable fields mirror the original JPA entities.
"""
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Column, Date, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, JSON, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthProviderType(str, Enum):
    GOOGLE = "GOOGLE"
    GITHUB = "GITHUB"
    EMAIL = "EMAIL"


exam_questions = Table(
    "exam_questions", Base.metadata,
    Column("exam_id", ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True),
    Column("question_id", ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_exam_questions_question_id", "question_id"),
)


class User(Base):
    __tablename__ = "app_user"
    __table_args__ = (Index("idx_provider_id_provider_type", "provider_id", "provider_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(255))
    middle_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    password: Mapped[str | None] = mapped_column(String(255))  # Store password hashes only.
    provider_id: Mapped[str | None] = mapped_column(String(255))
    provider_type: Mapped[AuthProviderType | None] = mapped_column(
        SAEnum(AuthProviderType, native_enum=False, create_constraint=True, name="auth_provider_type")
    )
    pdf_uploads: Mapped[list["PdfUpload"]] = relationship(back_populates="user", passive_deletes="all")
    exams: Mapped[list["Exam"]] = relationship(back_populates="user", passive_deletes="all")


class PdfUpload(Base):
    __tablename__ = "pdf_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, default=utc_now)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    user: Mapped[User] = relationship(back_populates="pdf_uploads")
    questions: Mapped[list["Question"]] = relationship(back_populates="pdf_upload", passive_deletes="all")
    exams: Mapped[list["Exam"]] = relationship(back_populates="pdf_upload", passive_deletes="all")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_text: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(255))
    difficulty: Mapped[str | None] = mapped_column(String(255))
    has_image: Mapped[bool | None] = mapped_column(Boolean)
    pdf_upload_id: Mapped[int | None] = mapped_column(ForeignKey("pdf_uploads.id"), index=True)
    pdf_upload: Mapped[PdfUpload | None] = relationship(back_populates="questions")
    options: Mapped[list["Option"]] = relationship(back_populates="question", cascade="all, delete-orphan", passive_deletes=True)
    exams: Mapped[list["Exam"]] = relationship(secondary=exam_questions, back_populates="questions", passive_deletes=True)


class Option(Base):
    __tablename__ = "options"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    question: Mapped[Question | None] = relationship(back_populates="options")


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utc_now)
    max_time: Mapped[int] = mapped_column(Integer, default=0)  # Minutes.
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    per_question_time_seconds: Mapped[int] = mapped_column(Integer, default=0)
    total_time_seconds: Mapped[int] = mapped_column(Integer, default=0)
    paper: Mapped[dict | None] = mapped_column(JSON)
    important_topics: Mapped[str | None] = mapped_column(Text)  # Comma-separated, as in Java.
    pdf_upload_id: Mapped[int | None] = mapped_column(ForeignKey("pdf_uploads.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    pdf_upload: Mapped[PdfUpload | None] = relationship(back_populates="exams")
    user: Mapped[User] = relationship(back_populates="exams")
    questions: Mapped[list[Question]] = relationship(secondary=exam_questions, back_populates="exams", passive_deletes=True)
