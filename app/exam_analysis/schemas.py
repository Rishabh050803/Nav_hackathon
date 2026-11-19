from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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

# History statistics are deterministic; AI consumes these results, never grades answers.


class HistoryMetrics(Summary):
    completion_percent: float | None
    average_time_spent_ms: float | None
    accuracy_interval_95: list[float] | None


class HistoryTopic(HistoryMetrics):
    topic: str | None
    exam_count: int
    classification: Literal['strong', 'weak', 'developing', 'insufficient_data']


class DifficultyHistory(HistoryMetrics):
    exam_count: int


class ExamHistoryItem(HistoryMetrics, Output):
    attempt_id: int
    exam_id: int
    submitted_at: datetime | None
    submission_reason: str | None
    duration_seconds: int


class Trend(BaseModel):
    window_size: int
    recent_exam_count: int
    previous_exam_count: int
    recent_mean_score_percent: float | None
    previous_mean_score_percent: float | None
    score_change_percentage_points: float | None
    recent_mean_accuracy_percent: float | None
    direction: Literal['insufficient_data', 'improving', 'declining', 'stable']


class Pacing(BaseModel):
    average_correct_time_ms: float | None
    average_incorrect_time_ms: float | None
    average_unattempted_time_ms: float | None
    rapid_incorrect_count: int
    revisited_question_count: int
    unvisited_question_count: int
    answer_transition_count: int
    focus_budget_utilization_percent: float | None


class Finding(BaseModel):
    id: str
    category: str
    topic: str | None
    message: str


class PerformanceView(Output):
    generated_at: datetime
    submitted_exam_count: int
    overall: HistoryMetrics
    mean_exam_score_percent: float | None
    median_exam_score_percent: float | None
    best_exam_score_percent: float | None
    worst_exam_score_percent: float | None
    score_standard_deviation: float | None
    current_80_percent_score_streak: int
    topics: list[HistoryTopic]
    strong_topics: list[str]
    weak_topics: list[str]
    by_difficulty: dict[str, DifficultyHistory]
    timeline: list[ExamHistoryItem]
    trend: Trend
    pacing: Pacing
    submission_reasons: dict[str, int]
    findings: list[Finding]
    limitations: list[str]


class Recommendation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    priority: Literal['high', 'medium', 'low']
    topic: str | None
    action: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class CoachingAdvice(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    overall_assessment: str = Field(min_length=1, max_length=2000)
    recommendations: list[Recommendation] = Field(min_length=1, max_length=10)


class SuggestionsView(Output):
    performance: PerformanceView
    source: Literal['ai', 'rules']
    fallback_reason: Literal['not_configured', 'unavailable', 'invalid_response', 'insufficient_history'] | None
    advice: CoachingAdvice
