"""Ground coaching in calculated evidence and retain advice during model outages."""
from app.exam_analysis import llm
from app.exam_analysis.schemas import CoachingAdvice


def payload_for(performance):
    # Whole-history aggregates plus bounded recent context; no account IDs,
    # question text, answers, event history, bearer tokens, or signing secrets.
    findings = sorted(performance['findings'], key=lambda f: (
        {'weak': 0, 'coverage': 1, 'developing': 2, 'trend': 3, 'timing': 4,
         'overall': 5, 'strong': 6}.get(f['category'], 7), f['id']))[:100]
    relevant = {f['topic'] for f in findings if f['topic'] is not None}
    topics = [t for t in performance['topics'] if t['topic'] in relevant][:50]
    allowed = {t['topic'] for t in topics}
    # Never send an evidence topic that was excluded from the bounded breakdown.
    findings = [f for f in findings if f['topic'] is None or f['topic'] in allowed]
    return dict(submitted_exam_count=performance['submitted_exam_count'], overall=performance['overall'],
        mean_exam_score_percent=performance['mean_exam_score_percent'],
        score_standard_deviation=performance['score_standard_deviation'],
        topics=topics, trend=performance['trend'], pacing=performance['pacing'],
        findings=findings, limitations=performance['limitations'],
        recent_exams=[{k: r[k] for k in ('score_percent', 'accuracy_percent', 'completion_percent',
            'total_questions', 'total_time_spent_ms', 'duration_seconds', 'submission_reason')}
            for r in performance['timeline'][-20:]])


def rule_advice(performance):
    if not performance['submitted_exam_count']:
        return CoachingAdvice(overall_assessment='No submitted exams yet. Complete an exam to establish your baseline.',
            recommendations=[dict(priority='high', topic=None, action='Complete one timed exam, then review every incorrect and unanswered question.',
                rationale='Submitted exam history is needed for a performance assessment.', evidence_ids=['history'])])
    overall = performance['overall']
    recommendations = []
    actions = {
        'weak': ('high', 'Review the core concepts, then practise a short untimed set. Explain each wrong answer before trying a timed set.'),
        'coverage': ('high', 'Review why these questions were left unanswered. Use a first pass for familiar questions and return to marked questions.'),
        'developing': ('medium', 'Practise mixed question types and keep an error log of missed concepts. Revisit the errors in your next session.'),
        'strong': ('low', 'Maintain this topic with spaced revision and a small mixed practice set.'),
        'timing': ('medium', 'Practise a timed set with checkpoints and reserve time to revisit unanswered questions.'),
        'trend': ('medium', 'Compare the recent exams by topic and difficulty before changing your study plan.'),
    }
    rank = {'weak': 0, 'coverage': 1, 'developing': 2, 'timing': 3, 'trend': 4, 'strong': 5}
    for finding in sorted(performance['findings'], key=lambda f: rank.get(f['category'], 9)):
        if finding['category'] in actions:
            priority, action = actions[finding['category']]
            recommendations.append(dict(priority=priority, topic=finding['topic'], action=action,
                rationale=finding['message'], evidence_ids=[finding['id']]))
        if len(recommendations) == 8:
            break
    if not recommendations:
        recommendations.append(dict(priority='medium', topic=None,
            action='Complete more exams covering the same topics, and review incorrect answers before each new attempt.',
            rationale='Topic labels require at least five answered questions across two exams.', evidence_ids=['overall']))
    return CoachingAdvice(overall_assessment=(
        f"Across {performance['submitted_exam_count']} submitted exams, your score is {overall['score_percent']}%, "
        f"answer accuracy is {overall['accuracy_percent']}%, and completion is {overall['completion_percent']}%. "
        f"There are {len(performance['strong_topics'])} strong and {len(performance['weak_topics'])} weak topics with sufficient evidence. "
        'Treat other topics as provisional until more answers are recorded.'), recommendations=recommendations)


def validate_grounding(advice, payload):
    evidence = {f['id']: f for f in payload['findings']}
    for recommendation in advice.recommendations:
        if any(key not in evidence for key in recommendation.evidence_ids):
            raise llm.InvalidAdvice
        if recommendation.topic is not None and not any(
            evidence[key]['topic'] == recommendation.topic for key in recommendation.evidence_ids
        ):
            raise llm.InvalidAdvice


def suggestions(performance):
    fallback = 'insufficient_history' if not performance['submitted_exam_count'] else None
    if fallback is None and not llm.configured():
        fallback = 'not_configured'
    if fallback is None:
        payload = payload_for(performance)
        try:
            advice = llm.generate_advice(payload)
            # Revalidate at the boundary even when an adapter is substituted.
            advice = CoachingAdvice.model_validate(advice)
            validate_grounding(advice, payload)
            return dict(performance=performance, source='ai', fallback_reason=None, advice=advice)
        except llm.AdviceUnavailable:
            fallback = 'unavailable'
        except (llm.InvalidAdvice, ValueError):
            fallback = 'invalid_response'
    return dict(performance=performance, source='rules', fallback_reason=fallback, advice=rule_advice(performance))
