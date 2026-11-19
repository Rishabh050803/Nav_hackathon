from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Attempt(Base):
    __tablename__ = 'exam_attempts'
    __table_args__ = (
        UniqueConstraint('exam_id', 'user_id', name='uq_attempt_exam_user'),
        CheckConstraint("status IN ('running', 'paused', 'submitted')", name='ck_attempt_status'),
        CheckConstraint('duration_seconds > 0', name='ck_attempt_duration'),
        Index('ix_attempt_expiry', 'status', 'deadline', 'pause_expires_at'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey('exams.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('app_user.id'), index=True)
    title: Mapped[str | None]
    status: Mapped[str] = mapped_column(default='running')
    duration_seconds: Mapped[int]
    started_at: Mapped[datetime]
    deadline: Mapped[datetime]
    paused_at: Mapped[datetime | None]
    pause_expires_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime | None]
    submission_reason: Mapped[str | None]
    active_question_id: Mapped[int | None]  # ID within this attempt's snapshot.
    focus_started_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1)
    questions: Mapped[list['AttemptQuestion']] = relationship(
        back_populates='attempt', cascade='all, delete-orphan', order_by='AttemptQuestion.position')
    events: Mapped[list['AttemptEvent']] = relationship(back_populates='attempt', cascade='all, delete-orphan')


class AttemptQuestion(Base):
    __tablename__ = 'attempt_questions'
    __table_args__ = (
        UniqueConstraint('attempt_id', 'source_question_id', name='uq_attempt_question'),
        UniqueConstraint('attempt_id', 'position', name='uq_attempt_position'),
        CheckConstraint('time_spent_ms >= 0', name='ck_question_time'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey('exam_attempts.id', ondelete='CASCADE'), index=True)
    source_question_id: Mapped[int]  # Deliberately not a FK: preserve historical content.
    position: Mapped[int]
    question_text: Mapped[str | None]
    topic: Mapped[str | None]
    difficulty: Mapped[str | None]
    has_image: Mapped[bool | None]
    options: Mapped[list] = mapped_column(JSON)  # Server-only correctness included in snapshot.
    selected_option_id: Mapped[int | None]
    marked_for_review: Mapped[bool] = mapped_column(default=False)
    time_spent_ms: Mapped[int] = mapped_column(default=0)
    visit_count: Mapped[int] = mapped_column(default=0)
    answer_change_count: Mapped[int] = mapped_column(default=0)
    first_visited_at: Mapped[datetime | None]
    answered_at: Mapped[datetime | None]
    attempt: Mapped[Attempt] = relationship(back_populates='questions')


class AttemptEvent(Base):
    __tablename__ = 'attempt_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey('exam_attempts.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str]
    occurred_at: Mapped[datetime]
    question_id: Mapped[int | None]
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt: Mapped[Attempt] = relationship(back_populates='events')
