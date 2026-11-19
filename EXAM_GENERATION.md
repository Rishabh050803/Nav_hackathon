# PrepX exam generation

The existing FastAPI application exposes the exam builder through its API and interactive Swagger UI at `/docs`. Run `alembic upgrade head` before starting the application.

## Module layout

Generation is isolated in `app/generation/`, alongside `app/conduction/`:

- `router.py`: generation and saved-paper HTTP endpoints.
- `schemas.py`: request validation and public response models.
- `service.py`: bank selection, generation orchestration, and paper persistence.
- `llm.py`: HTTP adapter for the question model.
- `dependencies.py`: trusted-caller authentication and database sessions.
- `utils.py`: shared normalization and difficulty definitions.

`app/main.py` registers both routers on one application and runs the conduction
worker. `Exam`, `Question`, and other shared database entities stay in
`app/models.py` because both features use them. Existing endpoint paths, request
formats, and authentication headers remain unchanged.

## Architecture

1. A trusted caller supplies the acting user's ID, the desired question count and time per question, and either a single difficulty or exact counts for easy, medium and hard.
2. The service loads eligible text-only multiple-choice questions from the shared bank and the user's own uploads. An optional PDF upload ID limits selection and model references to that upload. Bank rows need a nonblank stem, a recognized difficulty, two or more distinct labeled options, and exactly one marked correct answer. Difficulty and topic matching ignore case and repeated whitespace.
3. The selector fills the requested bank portion, preferring important topics and spreading picks across those topics when candidates permit. The remaining difficulty slots go to the model. Generated questions are directed toward the least represented important topics, with references ranked by topic and difficulty. Topic preferences are a weighting, and bank availability can limit coverage.
4. The model returns one four-option question per slot. The service checks structure, option uniqueness, answer index, and normalized duplicate stems against eligible bank questions and the other generated questions. Topic and difficulty labels come from the slot configuration. The model's semantic accuracy and originality beyond exact normalized text matching are unverified.
5. The service shuffles question order, calculates the total duration, and saves the paper and its bank associations in one database transaction. Reads return the saved order. The API omits answer keys and internal bank IDs.

## Database changes

Alembic revision `0003` adds nullable JSON column `exams.paper`. Existing exam rows retain their data and have no generated snapshot. The snapshot contains difficulty counts, important topics as an array, generated count, and ordered question data including option answer keys. Previous-year questions keep their existing `exam_questions` associations. Model output lives inside its exam snapshot, leaving the question bank available for previous-year material. Copies of the selected bank questions preserve the paper when bank rows change or are removed.

The existing timing fields store per-question and total seconds. Legacy `max_time` stores the total rounded up to the next whole minute. There is no new question-table migration or backfill. Downgrading from revision `0003` discards saved snapshots.

## Configuration

| Environment variable | Purpose |
| --- | --- |
| `EXAM_API_KEY` | Shared secret used by a trusted backend caller. |
| `EXAM_LLM_URL` | Full Chat Completions-compatible endpoint URL. |
| `EXAM_LLM_MODEL` | Model identifier supporting JSON object responses. |
| `EXAM_LLM_API_KEY` | Optional bearer token for the model endpoint. |

Use Authorization: Bearer <token> from the [authentication API](app/authentication/README.md) for both calls. Trusted server callers can also use X-Exam-Api-Key and X-User-Id. The trusted caller authenticates the end user and sets the user ID. Users and previous-year questions must already exist in the configured SQLite database. Unattached questions are treated as shared bank records. Model requests include reference stems and topic labels.

## API

### POST /api/exams/generate

```json
{
  "title": "Quantitative aptitude practice",
  "question_count": 10,
  "per_question_time_seconds": 75,
  "difficulty_counts": {"easy": 3, "medium": 5, "hard": 2},
  "important_topics": ["Probability", "Algebra"],
  "generated_question_count": 3
}
```

Send `difficulty` with `easy`, `medium` or `hard` for a single-difficulty paper, or send `difficulty_counts` for a mix. Omitted counts within the mix default to zero and the counts must sum to the requested paper size. The paper supports 1–100 questions, 1–3,600 seconds per question, up to 20 important topics, and up to 20 generated questions within the total. The generated count defaults to zero. In the example the service uses seven bank questions, generates three variants, and stores a 750-second time budget. Timing is stored in the shared exam timing fields. The optional `pdf_upload_id` scopes the bank to one owned upload.

Returns `201 Created` with the exam ID, title, creation timestamp (UTC), timing, requested counts and topics, optional upload ID, and ordered questions with position, text, topic, difficulty, source and labeled options.

### GET /api/exams/{exam_id}

Returns `200 OK` with the same paper representation for the acting user. Returns `404` for an unknown exam, one owned by a different user, or a legacy row without a snapshot.

| Status | Cause |
| --- | --- |
| `400` | Missing user ID header on an authenticated request. |
| `401` | Missing or invalid API key. |
| `404` | User, owned upload or generated paper unavailable. |
| `422` | Invalid request, insufficient eligible bank questions, or missing reference material. |
| `502` | Model response failed validation. |
| `503` | API access or the requested model service is unavailable. |

Failed model calls save no partial paper.
