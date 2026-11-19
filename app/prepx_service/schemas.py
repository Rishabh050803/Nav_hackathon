from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .languages import LANGUAGES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Choice(StrictModel):
    label: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1, max_length=10000)


class ExtractedQuestion(StrictModel):
    original_question_number: str | None = Field(max_length=255)
    question_text: str = Field(min_length=1, max_length=30000)
    options: list[Choice] = Field(min_length=2, max_length=20)
    topic: str | None = Field(max_length=255)
    languages: list[str] = Field(min_length=1)
    source_pages: list[int] = Field(min_length=1)
    correct_labels: list[str]
    difficulty_score: float = Field(ge=0, le=1)
    warnings: list[str]

    @field_validator('languages')
    @classmethod
    def allowed_languages(cls, values):
        if len(values) != len(set(values)) or any(v not in LANGUAGES for v in values):
            raise ValueError('Use unique supported Indian/English language codes')
        return values

    @model_validator(mode='after')
    def validate_options(self):
        labels = [o.label for o in self.options]
        if len(set(labels)) != len(labels) or len({v.casefold() for v in labels}) != len(labels):
            raise ValueError('Option labels must be unique')
        if len(set(self.correct_labels)) != len(self.correct_labels) or not set(self.correct_labels) <= set(labels):
            raise ValueError('Answers must refer to existing unique option labels')
        return self


class ExtractedDocument(StrictModel):
    examname: str | None = Field(max_length=255)
    questions: list[ExtractedQuestion] = Field(min_length=1, max_length=500)


class Answer(StrictModel):
    question_index: int = Field(ge=0)
    correct_labels: list[str]
    warning: str | None


class AnswerBatch(StrictModel):
    answers: list[Answer]


class QuestionResult(ExtractedQuestion):
    question_id: int
    level: Literal['easy', 'medium', 'difficult']
    examname: str
    correct_answer_text: str | None
    answer_source: Literal['pdf', 'groq', 'unresolved']
    has_image: bool


class DocumentResult(StrictModel):
    filename: str
    pdf_upload_id: int
    exam_id: int
    pages: int
    warnings: list[str]
    questions: list[QuestionResult]


class BatchResult(StrictModel):
    documents: list[DocumentResult]
