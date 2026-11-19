"""Grade frozen attempt questions and calculate the score."""

from typing import Literal

from app.conduction.models import AttemptQuestion

Outcome = Literal['correct', 'incorrect', 'unattempted']


def grade(question: AttemptQuestion) -> dict:
    correct_ids = [option['id'] for option in question.options if option['is_correct']]
    selected_id = question.selected_option_id
    status: Outcome = 'unattempted'
    if selected_id is not None:
        status = 'correct' if selected_id in correct_ids else 'incorrect'
    return dict(
        question_id=question.id, source_question_id=question.source_question_id,
        position=question.position, question_text=question.question_text,
        topic=question.topic, difficulty=question.difficulty, selected_option_id=selected_id,
        correct_option_ids=correct_ids, is_correct=status == 'correct', status=status,
        options=[{key: option[key] for key in ('id', 'label', 'text')} for option in question.options],
        time_spent_ms=question.time_spent_ms, visit_count=question.visit_count,
        answer_change_count=question.answer_change_count, marked_for_review=question.marked_for_review,
    )


def percent(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 2) if denominator else None


def summarize(rows: list[dict]) -> dict:
    correct = sum(row['status'] == 'correct' for row in rows)
    incorrect = sum(row['status'] == 'incorrect' for row in rows)
    attempted = correct + incorrect
    return dict(
        total_questions=len(rows), answered_count=attempted, correct_count=correct,
        incorrect_count=incorrect, unattempted_count=len(rows) - attempted,
        score_percent=percent(correct, len(rows)),
        accuracy_percent=percent(correct, attempted),
        total_time_spent_ms=sum(row['time_spent_ms'] for row in rows),
    )
