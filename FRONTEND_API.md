# Frontend API reference

Verified against the current route registration and response schemas.
Default development base URL: `http://localhost:8000`.

## Entrypoints and authentication

`uvicorn app.main:app --reload` exposes 15 business endpoints.
`uvicorn app.prepx_service.main:app --reload` also installs the two PDF ingestion
endpoints (17 total), after installing `app/prepx_service/requirements.txt` and
configuring ingestion. The current root virtual environment is missing
`pydantic_settings`, so the combined entrypoint needs its dependencies installed.

- **Bearer**: `Authorization: Bearer <token>`. Applies to conduction, evaluation,
  analysis, and ingestion. The authenticated user owns the requested records.
- **Generation**: `X-Exam-Api-Key: <EXAM_API_KEY>` and `X-User-Id: <positive integer>`.
  These belong on a trusted backend/proxy. Do not embed the shared key in browser
  code. The proxy must authenticate the user and derive the ID.
- JSON requests use `Content-Type: application/json`. No JSON header/body is
  required for GET or the bodyless start/suggestions calls. File uploads use
  multipart FormData; let the browser set its Content-Type and boundary.
- Login, signup, token refresh, user/exam listing, and timing-edit endpoints are
  not implemented. Local developers can mint a token for an existing user with
  `python -m app.auth USER_ID --ttl 7200`; the server uses the same signing secret.
- The current app does not configure CORS. Use a same-origin proxy for a browser
  frontend, or configure allowed frontend origins before direct cross-origin use.

## Endpoint inventory

All placeholders are integer IDs. Successful responses are JSON. All endpoints
return 200 except generation, which returns 201. The current routes have no query
parameters.

### Generation — generation headers

| Method | Endpoint | Required input | Returns / purpose |
| --- | --- | --- | --- |
| POST | `/api/exams/generate` | `GenerateExam` JSON below | 201 `ExamPaper`; select bank questions, optionally generate variants, and persist paper |
| GET | `/api/exams/{exam_id}` | Owned exam ID | `ExamPaper`; retrieve saved generated paper, 404 for legacy paper without snapshot |

`GenerateExam` fields:

| Field | Required / default | Validation |
| --- | --- | --- |
| `question_count` | Required | Integer 1–100 |
| `per_question_time_seconds` | Required | Integer 1–3600 |
| `difficulty` | Exactly one of this or `difficulty_counts` | `easy`, `medium`, `hard` |
| `difficulty_counts` | Exactly one of this or `difficulty` | Object with nonnegative integer `easy`, `medium`, `hard`; omitted keys default to 0; sum must equal question_count |
| `title` | Default `Practice exam` | Nonblank trimmed string, max 255 characters |
| `important_topics` | Default `[]` | Up to 20 unique nonblank strings, each max 255 characters |
| `generated_question_count` | Default 0 | Integer 0–20 and <= question_count |
| `pdf_upload_id` | Default null | Positive ID of user's upload; limits source bank |

Example request:

```json
{
  "title": "Math practice",
  "question_count": 10,
  "per_question_time_seconds": 60,
  "difficulty_counts": {"easy": 3, "medium": 5, "hard": 2},
  "important_topics": ["Algebra", "Geometry"],
  "generated_question_count": 2
}
```

`ExamPaper` contains:

```text
id, title, created_at, question_count, per_question_time_seconds,
total_time_seconds, difficulty_counts, important_topics,
generated_question_count, pdf_upload_id,
questions: [{position, question_text, difficulty, topic,
             source: "previous_year" | "generated",
             options: [{label, text}]}]
```

Paper questions/options do not expose selection IDs or answer keys. To render a
conducted exam and select answers, obtain `AttemptView` from start instead.
Generation requires eligible source questions and, when generating new variants,
a configured model. Failed generation does not save a partial exam.

### Conduction — bearer authentication

`a` means attempt ID and `q` means the attempt question ID from `AttemptView`.

| Method | Endpoint | JSON body | Returns / purpose |
| --- | --- | --- | --- |
| POST | `/api/exams/{exam_id}/attempt` | None | `AttemptView`; start or recover the existing attempt without resetting it |
| GET | `/api/attempts/{a}` | None | `AttemptView`; recover questions, answers, flags, timer, and status |
| PUT | `/api/attempts/{a}/questions/{q}/answer` | `{"version": 1, "option_id": 7}` | `AttemptView`; immediately save/replace answer; null clears it |
| POST | `/api/attempts/{a}/questions/{q}/visit` | `{"version": 2}` | `AttemptView`; navigate to question and checkpoint prior question time |
| PUT | `/api/attempts/{a}/questions/{q}/review` | `{"version": 3, "marked": true}` | `AttemptView`; toggle review flag |
| POST | `/api/attempts/{a}/pause` | `{"version": 4}` | `AttemptView`; freeze timer and return pause expiry |
| POST | `/api/attempts/{a}/resume` | `{"version": 5}` | `AttemptView`; resume before the 15-minute pause deadline |
| POST | `/api/attempts/{a}/submit` | `{"version": 6}` | `AttemptView`; finalize answers, also allowed while paused |
| POST | `/api/attempts/{a}/heartbeat` | `{"version": 7}` | `AttemptView`; checkpoint focus time and return current state |

Versions above are illustrative; always send the latest response's version.
The option field is required for an answer request, even when its value is null.
IDs and versions must be positive integers; `marked` must be a JSON boolean.

`AttemptView` fields:

```text
id, exam_id, title,
status: "running" | "paused" | "submitted",
version, server_time, started_at,
deadline, pause_expires_at, remaining_seconds, duration_seconds,
submitted_at, submission_reason, active_question_id, answered_count,
questions: [{id, source_question_id, position, question_text, topic, difficulty,
             has_image, options: [{id, label, text}], selected_option_id,
             marked_for_review, time_spent_ms, visit_count, answer_change_count}]
```

- `id` inside questions is the **attempt question ID** used in visit/answer/review
  paths. `source_question_id` is a bank reference and must not replace it.
- `option_id` comes from that question's `options[].id`.
- `deadline` is non-null only while running; `pause_expires_at` only while paused.
- `submission_reason`: null before submission, then `manual`, `time_expired`, or
  `pause_expired`. Submitted attempts return remaining_seconds=0.
- Conduction timestamps use UTC with `Z`. Generation's created_at is naive UTC in
  the current schema; treat it as UTC if its JSON representation has no suffix.
- Closing a tab does not pause. Automatic submission is server-side. Resume must
  arrive strictly before 15 minutes after pausing. Submitted answers are final.
- Correctness is excluded from these responses. Focus time is milliseconds;
  deadline/remaining duration fields explicitly use seconds.

### Evaluation and per-exam analysis — bearer authentication

Both routes require a submitted owned attempt. Otherwise they return 409. An
expired attempt is finalized before the response is calculated.

| Method | Endpoint | Requires | Returns |
| --- | --- | --- | --- |
| GET | `/api/attempts/{attempt_id}/evaluation` | Attempt ID; no body | `EvaluationView`: grading totals |
| GET | `/api/attempts/{attempt_id}/analysis` | Attempt ID; no body | `AnalysisView`: grading, question review, topic/difficulty breakdowns, time and events |

`Summary` (shared statistics object):

```text
total_questions, answered_count, correct_count, incorrect_count,
unattempted_count, score_percent, accuracy_percent, total_time_spent_ms
```

Score = correct/all; accuracy = correct/answered. A zero denominator produces
null, not zero. Scoring currently counts correct single-option answers, with no
negative marking. `EvaluationView` is `Summary` plus `attempt_id`.

`AnalysisView` adds these fields to `EvaluationView`:

```text
questions: [{question_id, source_question_id, position, question_text, topic,
             difficulty, selected_option_id, correct_option_ids, is_correct,
             status: "correct" | "incorrect" | "unattempted",
             options: [{id, label, text}], time_spent_ms, visit_count,
             answer_change_count, marked_for_review}],
topics: [{...Summary, topic,
          classification: "strong" | "weak" | "developing" | "insufficient_data"}],
strong_topics: [string], weak_topics: [string],
by_difficulty: {difficultyName: Summary},
average_time_spent_ms, unvisited_count,
events: [{id, kind, occurred_at, question_id, details}]
```

### User history and AI coaching — bearer authentication

| Method | Endpoint | Requires | Returns |
| --- | --- | --- | --- |
| GET | `/api/analysis/performance` | No body or user ID | `PerformanceView`: authenticated user's submitted exam history and statistics |
| POST | `/api/analysis/suggestions` | No body or user ID | `SuggestionsView`: history plus prioritized coaching |

`HistoryMetrics` extends `Summary` with:

```text
completion_percent, average_time_spent_ms,
accuracy_interval_95: [lowerPercent, upperPercent] | null
```

`PerformanceView` fields:

```text
generated_at, submitted_exam_count, overall: HistoryMetrics,
mean_exam_score_percent, median_exam_score_percent,
best_exam_score_percent, worst_exam_score_percent, score_standard_deviation,
current_80_percent_score_streak,
topics: [{...HistoryMetrics, topic, exam_count, classification}],
strong_topics: [string], weak_topics: [string],
by_difficulty: {difficultyName: {...HistoryMetrics, exam_count}},
timeline: [{...HistoryMetrics, attempt_id, exam_id, submitted_at,
            submission_reason, duration_seconds}],
trend: {window_size, recent_exam_count, previous_exam_count,
        recent_mean_score_percent, previous_mean_score_percent,
        score_change_percentage_points, recent_mean_accuracy_percent,
        direction: "improving" | "declining" | "stable" | "insufficient_data"},
pacing: {average_correct_time_ms, average_incorrect_time_ms,
         average_unattempted_time_ms, rapid_incorrect_count,
         revisited_question_count, unvisited_question_count,
         answer_transition_count, focus_budget_utilization_percent},
submission_reasons: {reason: count},
findings: [{id, category, topic, message}],
limitations: [string]
```

History topics need at least five answers across two exams to classify. Strong
means >=80% accuracy, weak <50%, otherwise developing. The trend compares up to
five recent exams with the preceding up to five, without difficulty adjustment.
Empty history is a 200 response with zero counts, null percentages, and empty
arrays. These history classification thresholds differ from per-exam analysis.

`SuggestionsView`:

```text
performance: PerformanceView,
source: "ai" | "rules",
fallback_reason: null | "not_configured" | "unavailable" |
                 "invalid_response" | "insufficient_history",
advice: {
  overall_assessment: string,
  recommendations: [{priority: "high" | "medium" | "low", topic,
                     action, rationale, evidence_ids: [string]}]
}
```

Link recommendation evidence IDs to `performance.findings[].id`. Display `source`
so fallback advice is not presented as AI-generated. Model outages return 200
with rule-based advice and existing statistics. Allow more than 30 seconds for
this request; the external model request itself has a 30-second timeout.

### PDF ingestion — combined entrypoint only; bearer authentication

| Method | Endpoint | Requires | Returns |
| --- | --- | --- | --- |
| GET | `/api/v1/languages` | No body | Installed/available language capabilities and note |
| POST | `/api/v1/ingestions` | Multipart fields below | `BatchResult`: uploaded document IDs, exam IDs, extracted questions and warnings |

`GET /api/v1/languages` response:

```text
languages: [{code, name, tesseract_model, installed, easyocr_model}],
note: string
```

`installed` refers to Tesseract availability. EasyOCR model mapping does not mean
its weights are downloaded locally.

Multipart fields for ingestion:

| Field | Required / default | Meaning |
| --- | --- | --- |
| `files` | Required, repeated field | PDFs; defaults: <=5 files, 1–10 pages each, <=20 MB/file |
| `exam_id` | Optional positive ID | Append to owned exam; omit to create one exam per PDF |
| `examname` | Optional, <=255 characters | New exam name; does not replace existing exam titles |
| `languages` | Optional string | Comma-separated supported codes, e.g. `en,hi` |
| `ocr_backend` | Default `tesseract` | `tesseract` or `easyocr` |
| `force_ocr` | Default false | Boolean; OCR even if a native text layer exists |

`BatchResult`:

```text
documents: [{filename, pdf_upload_id, exam_id, pages, warnings: [string],
  questions: [{question_id, original_question_number, question_text,
    options: [{label, text}], topic, languages: [string], source_pages: [integer],
    correct_labels: [string], difficulty_score, warnings: [string],
    level: "easy" | "medium" | "difficult", examname,
    correct_answer_text, answer_source: "pdf" | "groq" | "unresolved", has_image}]}]
```

The ingestion response contains correct answers for source-review workflows;
conduction hides answers from candidates. All files in a batch commit together.
Duplicate uploads are not deduplicated. Newly ingested exams have no inferred
time budget; a positive duration must be configured before conduction. No timing
update endpoint currently exists. Ingestion's `difficult` label differs from
generation's `hard` filter in the current code; integration must account for it.

## Errors and client behavior

Most application errors use `{"detail": "message"}`. FastAPI validation errors
use `{"detail": [{"loc": [...], "msg": "...", "type": "...", ...}]}`.

| Status | Meaning |
| --- | --- |
| 400 | Missing generation user header with a valid shared key |
| 401 | Missing/invalid/expired authentication |
| 404 | Record missing, owned by another user, or no generated paper snapshot |
| 409 | Stale version, submitted attempt, invalid transition, or analysis requested before submission |
| 413 | Ingestion file size, batch size, or text limit exceeded; invalid page count returns 422 |
| 422 | Invalid fields, insufficient eligible questions, invalid PDF, or missing positive exam duration |
| 429 | Another ingestion batch is processing in that server process |
| 502 | Model returned invalid/rejected data in generation/ingestion |
| 503 | Missing server configuration, unavailable model/OCR, or ingestion database operation failure |

AI coaching model failures are an exception: its endpoint returns 200 with
`source="rules"` and a fallback_reason rather than failing the whole dashboard.

Queue attempt mutations, update the local version from every successful response,
and show an answer as saved only after success. On 409 refetch attempt state and
reconcile the intended edit; do not blindly overwrite another tab's newer answer.
Call visit whenever the displayed question changes. Heartbeats around every 15
seconds checkpoint timing but never extend the deadline. Use server_time and
remaining_seconds for the countdown, refetch at zero/reconnect, and treat submitted
as final. Pause only via an explicit successful pause request.

## Machine-readable specification

The 15 main endpoints are exported in [docs/openapi.json](docs/openapi.json).
Runtime documentation: `/docs`, `/redoc`, and `/openapi.json`. These documentation
routes do not require authentication. The combined entrypoint's runtime OpenAPI
also includes ingestion routes. Schemas in OpenAPI are authoritative for exact
field types, nullability, and validation constraints.
