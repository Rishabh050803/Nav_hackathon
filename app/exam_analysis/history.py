"""Aggregate submitted attempt snapshots; no model-generated statistics."""
from collections import Counter, defaultdict
from math import sqrt
from statistics import mean, median, pstdev

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.conduction.models import Attempt
from app.conduction.service import settle
from app.exam_evaluation.service import grade, percent, summarize
from app.generation.utils import normalize

RECENT_WINDOW = 5
MIN_TOPIC_ANSWERS = 5
MIN_TOPIC_EXAMS = 2


def load_history(session, user_id, now):
    attempts = list(session.scalars(select(Attempt).where(Attempt.user_id == user_id)
        .options(selectinload(Attempt.questions))
        .order_by(Attempt.submitted_at, Attempt.id)))
    for attempt in attempts:
        settle(attempt, now)
    return sorted((a for a in attempts if a.status == 'submitted'),
                  key=lambda a: (a.submitted_at or a.started_at, a.id))


def average(values):
    return round(mean(values), 2) if values else None


def confidence_interval(correct, answered):
    """95% Wilson interval for observed answer accuracy (descriptive estimate)."""
    if not answered:
        return None
    z = 1.96
    p = correct / answered
    denominator = 1 + z * z / answered
    center = (p + z * z / (2 * answered)) / denominator
    margin = z * sqrt(p * (1 - p) / answered + z * z / (4 * answered * answered)) / denominator
    return [round(100 * max(0, center - margin), 2), round(100 * min(1, center + margin), 2)]


def metrics(rows):
    summary = summarize(rows)
    return dict(**summary,
        completion_percent=percent(summary['answered_count'], len(rows)),
        average_time_spent_ms=average([r['time_spent_ms'] for r in rows]),
        accuracy_interval_95=confidence_interval(summary['correct_count'], summary['answered_count']))


def classify(summary, exams, known=True):
    if not known or summary['answered_count'] < MIN_TOPIC_ANSWERS or exams < MIN_TOPIC_EXAMS:
        return 'insufficient_data'
    if summary['accuracy_percent'] >= 80:
        return 'strong'
    if summary['accuracy_percent'] < 50:
        return 'weak'
    return 'developing'


def build_history(attempts, now):
    all_rows, timeline = [], []
    groups, difficulty_groups = defaultdict(list), defaultdict(list)
    for attempt in attempts:
        rows = [grade(q) for q in attempt.questions]
        all_rows.extend(rows)
        summary = metrics(rows)
        timeline.append(dict(attempt_id=attempt.id, exam_id=attempt.exam_id,
            submitted_at=attempt.submitted_at, submission_reason=attempt.submission_reason,
            duration_seconds=attempt.duration_seconds, **summary))
        for row in rows:
            groups[normalize(row['topic'])].append((attempt.id, row))
            difficulty_groups[normalize(row['difficulty']) or 'unspecified'].append((attempt.id, row))
    topics = []
    for key, entries in sorted(groups.items()):
        summary = metrics([r for _, r in entries])
        exams = len({aid for aid, _ in entries})
        topics.append(dict(topic=entries[0][1]['topic'].strip() if key else None,
            exam_count=exams, classification=classify(summary, exams, bool(key)), **summary))
    difficulties = {}
    for key, entries in sorted(difficulty_groups.items()):
        difficulties[key] = dict(exam_count=len({aid for aid, _ in entries}),
                                 **metrics([r for _, r in entries]))
    scores = [r['score_percent'] for r in timeline if r['score_percent'] is not None]
    recent = timeline[-RECENT_WINDOW:]
    previous = timeline[-2 * RECENT_WINDOW:-RECENT_WINDOW]
    recent_score = average([r['score_percent'] for r in recent if r['score_percent'] is not None])
    previous_score = average([r['score_percent'] for r in previous if r['score_percent'] is not None])
    delta = round(recent_score - previous_score, 2) if recent_score is not None and previous_score is not None else None
    recent_accuracy = average([r['accuracy_percent'] for r in recent if r['accuracy_percent'] is not None])
    trend = 'insufficient_data' if delta is None else ('improving' if delta > 5 else 'declining' if delta < -5 else 'stable')
    streak = 0
    for score in reversed([r['score_percent'] for r in timeline]):
        if score is None or score < 80:
            break
        streak += 1
    overall = metrics(all_rows)
    findings = []
    if not attempts:
        findings.append(dict(id='history', category='history', topic=None, message='No submitted exam history is available.'))
    if attempts:
        findings.append(dict(id='overall', category='overall', topic=None,
            message=f"{len(attempts)} submitted exams; score {overall['score_percent']}%; accuracy {overall['accuracy_percent']}%; completion {overall['completion_percent']}%."))
    for index, topic in enumerate(topics):
        if topic['classification'] in ('strong', 'weak', 'developing'):
            findings.append(dict(id=f'topic-{index}', category=topic['classification'], topic=topic['topic'],
                message=f"{topic['topic']}: {topic['classification']}, {topic['accuracy_percent']}% accuracy from {topic['answered_count']} answers across {topic['exam_count']} exams."))
        if topic['unattempted_count'] and topic['topic']:
            findings.append(dict(id=f'coverage-{index}', category='coverage', topic=topic['topic'],
                message=f"{topic['topic']}: {topic['unattempted_count']} of {topic['total_questions']} questions left unanswered."))
    if delta is not None:
        findings.append(dict(id='trend', category='trend', topic=None,
            message=f"Recent {len(recent)} exam mean score changed by {delta} percentage points compared with the preceding {len(previous)} exams; exam difficulty may differ."))
    expired = sum(a.submission_reason == 'time_expired' for a in attempts)
    if expired:
        findings.append(dict(id='timing', category='timing', topic=None,
            message=f'{expired} exams submitted at the time limit. Practise pacing; reaching the deadline alone does not prove poor time management.'))
    return dict(generated_at=now, submitted_exam_count=len(attempts), overall=overall,
        mean_exam_score_percent=average(scores), median_exam_score_percent=round(median(scores), 2) if scores else None,
        best_exam_score_percent=max(scores) if scores else None,
        worst_exam_score_percent=min(scores) if scores else None,
        score_standard_deviation=round(pstdev(scores), 2) if scores else None,
        current_80_percent_score_streak=streak,
        topics=topics, strong_topics=[t['topic'] for t in topics if t['classification'] == 'strong'],
        weak_topics=[t['topic'] for t in topics if t['classification'] == 'weak'],
        by_difficulty=difficulties, timeline=timeline,
        trend=dict(window_size=RECENT_WINDOW, recent_exam_count=len(recent), previous_exam_count=len(previous),
            recent_mean_score_percent=recent_score, previous_mean_score_percent=previous_score,
            score_change_percentage_points=delta, recent_mean_accuracy_percent=recent_accuracy, direction=trend),
        pacing=dict(average_correct_time_ms=average([r['time_spent_ms'] for r in all_rows if r['status'] == 'correct']),
            average_incorrect_time_ms=average([r['time_spent_ms'] for r in all_rows if r['status'] == 'incorrect']),
            average_unattempted_time_ms=average([r['time_spent_ms'] for r in all_rows if r['status'] == 'unattempted']),
            rapid_incorrect_count=sum(r['status'] == 'incorrect' and r['time_spent_ms'] <= 5000 for r in all_rows),
            revisited_question_count=sum(r['visit_count'] > 1 for r in all_rows),
            unvisited_question_count=sum(r['visit_count'] == 0 for r in all_rows),
            answer_transition_count=sum(r['answer_change_count'] for r in all_rows),
            focus_budget_utilization_percent=percent(overall['total_time_spent_ms'], sum(a.duration_seconds * 1000 for a in attempts))),
        submission_reasons=dict(Counter(a.submission_reason or 'unspecified' for a in attempts)), findings=findings,
        limitations=[
            'Statistics include submitted attempts only; no peer ranking or prediction is inferred.',
            'Topic labels require at least 5 answered questions across 2 exams; repeated questions are not independent samples.',
            'Scores count correct answers out of all questions; accuracy uses answered questions only. Unanswered questions affect completion.',
            'Trend compares up to 5 recent exams with the preceding up to 5; differences in difficulty and exam length can affect scores.',
            'Focus time measures the active question, not attention; pauses are excluded. Rapid answers do not establish guessing.',
            'The Wilson interval describes observed accuracy; question selection and repetition can limit its interpretation.',
        ])
