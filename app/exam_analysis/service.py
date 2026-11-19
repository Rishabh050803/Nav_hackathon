"""Report performance from finalized answers and server-recorded focus time."""

from collections import defaultdict

from app.conduction.models import Attempt
from app.exam_evaluation.service import grade, summarize
from app.generation.utils import normalize


def topic_classification(topic: str | None, summary: dict) -> str:
    attempted = summary['answered_count']
    correct = summary['correct_count']
    if topic is None or not attempted:
        return 'insufficient_data'
    if correct * 5 >= attempted * 4:
        return 'strong'
    if correct * 2 < attempted:
        return 'weak'
    return 'developing'


def build_report(attempt: Attempt) -> dict:
    rows = [grade(question) for question in attempt.questions]
    by_topic = defaultdict(list)
    by_difficulty = defaultdict(list)
    for row in rows:
        by_topic[normalize(row['topic'])].append(row)
        by_difficulty[normalize(row['difficulty']) or 'unspecified'].append(row)
    topics = []
    for key, group in sorted(by_topic.items()):
        topic = group[0]['topic'].strip() if key else None
        summary = summarize(group)
        topics.append(dict(topic=topic, classification=topic_classification(topic, summary), **summary))
    summary = summarize(rows)
    return dict(
        attempt_id=attempt.id, **summary, questions=rows, topics=topics,
        strong_topics=[topic['topic'] for topic in topics if topic['classification'] == 'strong'],
        weak_topics=[topic['topic'] for topic in topics if topic['classification'] == 'weak'],
        by_difficulty={difficulty: summarize(group) for difficulty, group in sorted(by_difficulty.items())},
        average_time_spent_ms=round(summary['total_time_spent_ms'] / len(rows), 2) if rows else None,
        unvisited_count=sum(row['visit_count'] == 0 for row in rows),
        events=[dict(id=event.id, kind=event.kind, occurred_at=event.occurred_at,
                     question_id=event.question_id, details=event.details)
                for event in sorted(attempt.events, key=lambda event: event.id)],
    )
