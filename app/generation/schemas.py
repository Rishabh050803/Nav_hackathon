"""Request validation and public exam paper representations."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.generation.utils import Difficulties, normalize

Difficulty = Literal["easy", "medium", "hard"]
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

