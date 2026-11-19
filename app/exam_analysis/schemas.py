from datetime import datetime
from typing import Literal

from app.conduction.schemas import OptionView, Output
from app.exam_evaluation.schemas import EvaluationView, Summary
from app.exam_evaluation.service import Outcome


class AnalysisQuestion(Output):
    question_id: int
    source_question_id: int
    position: int
    question_text: str | None
    topic: str | None
    difficulty: str | None
    selected_option_id: int | None
    correct_option_ids: list[int]
    is_correct: bool
    status: Outcome
    options: list[OptionView]
    time_spent_ms: int
    visit_count: int
    answer_change_count: int
    marked_for_review: bool


class EventView(Output):
    id: int
    kind: str
    occurred_at: datetime
    question_id: int | None
    details: dict


class TopicBreakdown(Summary):
    topic: str | None
    classification: Literal['strong', 'weak', 'developing', 'insufficient_data']


class AnalysisView(EvaluationView):
    questions: list[AnalysisQuestion]
    topics: list[TopicBreakdown]
    strong_topics: list[str]
    weak_topics: list[str]
    by_difficulty: dict[str, Summary]
    average_time_spent_ms: float | None
    unvisited_count: int
    events: list[EventView]
