# Exam history analysis and AI coaching

The existing `GET /api/attempts/{attempt_id}/analysis` report is retained.
Two history endpoints use the same signed bearer token as exam conduction.
They derive the user from authentication and never accept another user's ID.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/analysis/performance` | Whole-history statistics and findings |
| POST | `/api/analysis/suggestions` | Statistics plus AI or rule-based coaching; no request body |

```sh
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/analysis/performance
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/analysis/suggestions
```

## Statistics

History includes submitted attempts only, sorted by submission time then ID.
Overdue attempts are finalized before calculation, following existing deadline
rules. An empty history returns zero counts and null percentages, not invented
scores. No additional tables or migrations are required.

- `overall`: correct, incorrect, unanswered, accuracy, score, completion, time,
  and a 95% Wilson interval for observed answer accuracy.
- Score is correct / all questions; accuracy is correct / answered; completion is
  answered / all. Overall ratios are weighted by questions, not exams.
- Exam-level mean, median, best, worst, population standard deviation, and the
  current consecutive streak of exams scoring at least 80%.
- `timeline`: each submitted exam's results, duration, and submission reason.
- Topic and difficulty breakdowns, normalized for case and repeated whitespace.
  Topics require at least 5 answers across at least 2 exams for classification:
  strong >=80% accuracy, weak <50%, developing otherwise. Unknown topics or
  smaller samples are `insufficient_data`. Skipped topics get coverage findings
  even if accuracy cannot be assessed. These history thresholds are separate from
  the existing per-exam topic labels.
- `trend`: the mean score of up to 5 latest exams vs the preceding up to 5.
  A change greater than +5 percentage points is improving, less than -5 declining,
  otherwise stable. Without a previous window it is insufficient data. This is
  descriptive; it does not adjust for difficulty or exam length.
- `pacing`: mean correct/incorrect/unanswered question time, incorrect responses
  within 5 seconds, revisits, unvisited questions, saved answer transitions, and
  total active question time / total exam time budget. Saved transitions include
  first selection and clearing. Time measures focus duration, not attention;
  pauses are excluded and rapid answers do not establish guessing.
- Submission reason counts and evidence-labelled findings for coaching.

Repeated questions are counted as exam observations, not unique knowledge tests.
The confidence interval does not establish independent sampling or predict future
results. No peer ranking, percentile, or pass probability is inferred.

## AI configuration and behavior

Set `ANALYSIS_LLM_URL` to a full Chat Completions-compatible URL,
`ANALYSIS_LLM_MODEL`, and optional `ANALYSIS_LLM_API_KEY`. Missing values reuse
the corresponding `EXAM_LLM_*` configuration. The root app reads exported
environment variables, not `.env` files automatically.

AI receives whole-history aggregates, up to 50 topic breakdowns, 100 findings,
and 20 recent exam summaries. It does not receive account identifiers, names,
email addresses, exam titles, question text, selected options, answer keys, or raw
event history. Topic labels are supplied as untrusted data. Database transactions
are released before the external request. The request has a 30-second timeout
and bounded response size, and its output is validated against a strict schema.

The response includes:

- `performance`: calculated statistics, strengths, weaknesses, and limitations.
- `source`: `ai` or `rules`.
- `fallback_reason`: null for AI, otherwise `not_configured`, `unavailable`,
  `invalid_response`, or `insufficient_history`.
- `advice.overall_assessment`: performance narrative.
- `advice.recommendations`: prioritized practice actions, topic, rationale, and
  `evidence_ids` referencing `performance.findings`.

Unknown evidence IDs or topics unsupported by the cited evidence cause fallback.
Rule-based coaching remains available without model credentials and on model
errors; it is explicitly labelled and not represented as AI-generated. The empty
history response recommends establishing a baseline without making a model call.
AI text is advisory; calculated statistics are authoritative. Coaching is computed
on demand and is not cached or persisted.

## Modules and verification

`history.py` calculates statistics, `coaching.py` assembles evidence and fallback
advice, `llm.py` handles the model request, `routes.py` authenticates HTTP requests,
and `schemas.py` validates public output. Existing per-attempt logic remains in
`service.py`.

```sh
python -m unittest discover -s tests -v
python tests/live_endpoints.py
```

AI success, malformed output, oversized responses, unavailable models, and invalid
evidence are tested with mocked model responses. Live HTTP tests use deterministic
fallback, so they do not make paid provider calls.
