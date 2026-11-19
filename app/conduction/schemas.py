from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class Output(BaseModel):
    @field_serializer('*', when_used='json')
    def utc_dates(self, value):
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
        return value


class Versioned(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1, strict=True)


class AnswerRequest(Versioned):
    option_id: int | None = Field(..., gt=0, strict=True)


class ReviewRequest(Versioned):
    marked: bool = Field(strict=True)


class OptionView(Output):
    id: int
    label: str | None
    text: str | None


class QuestionView(Output):
    id: int
    source_question_id: int
    position: int
    question_text: str | None
    topic: str | None
    difficulty: str | None
    has_image: bool | None
    options: list[OptionView]
    selected_option_id: int | None
    marked_for_review: bool
    time_spent_ms: int
    visit_count: int
    answer_change_count: int


class AttemptView(Output):
    id: int
    exam_id: int
    title: str | None
    status: Literal['running', 'paused', 'submitted']
    version: int
    server_time: datetime
    started_at: datetime
    deadline: datetime | None
    pause_expires_at: datetime | None
    remaining_seconds: int
    duration_seconds: int
    submitted_at: datetime | None
    submission_reason: str | None
    active_question_id: int | None
    answered_count: int
    questions: list[QuestionView]


class AnalysisQuestion(Output):
    question_id: int
    source_question_id: int
    topic: str | None
    difficulty: str | None
    selected_option_id: int | None
    correct_option_ids: list[int]
    is_correct: bool
    time_spent_ms: int
    visit_count: int
    answer_change_count: int


class EventView(Output):
    id: int
    kind: str
    occurred_at: datetime
    question_id: int | None
    details: dict


class AnalysisView(Output):
    attempt_id: int
    total_questions: int
    answered_count: int
    correct_count: int
    total_time_spent_ms: int
    questions: list[AnalysisQuestion]
    events: list[EventView]
