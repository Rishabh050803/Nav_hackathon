from pydantic import BaseModel


class Summary(BaseModel):
    total_questions: int
    answered_count: int
    correct_count: int
    incorrect_count: int
    unattempted_count: int
    score_percent: float | None
    accuracy_percent: float | None
    total_time_spent_ms: int


class EvaluationView(Summary):
    attempt_id: int
