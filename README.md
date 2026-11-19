# PrepX FastAPI backend

Python backend inspired by the original Java project in
`../PrepX-exam_creation/backend/prepx`. This directory is the standalone PrepX project.
Python 3.10+ is required. See [Exam generation](EXAM_GENERATION.md) for architecture, configuration, schema changes, and API examples.

## Setup

Run from this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
alembic upgrade head
export AUTH_TOKEN_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
uvicorn app.main:app --reload
```

Keep the same secret across restarts and workers; changing it invalidates tokens.
`DATABASE_URL` defaults to `sqlite:///./prepx.db`. Relative filenames resolve
against this directory. Export environment variables explicitly; `.env` files
are not automatically loaded. Interactive API documentation is at `/docs`.

User registration, PDF extraction, and a browser UI are outside this branch.
Exam generation is implemented in `app/generation/`; see [Exam generation](EXAM_GENERATION.md)
for its configuration and trusted-caller authentication headers. Populate the existing `User`, `Exam`, `Question`, and `Option`
models through your existing workflow or a trusted local script first.

## Conduction rules

- Only the exam owner can start or access its attempt. One attempt per exam/user;
  retrying start returns the saved attempt, including after submission.
- Duration uses `Exam.total_time_seconds`, falling back to `max_time * 60`.
  Per-question time is metadata, not a separate forced question deadline.
- Starting freezes question content, option IDs, correct answers, and question
  order (source question ID order). Later source edits do not change an attempt.
- Selecting an option saves immediately; selecting another replaces it. Send
  `option_id: null` to clear an answer. Unanswered questions are permitted.
- Navigation is unrestricted while running. Questions may be marked for review.
- Pausing freezes the remaining exam budget. Resume must arrive **strictly before
  15 minutes** from that pause. At 15 minutes the attempt is submitted. Each pause
  has its own window; no total pause-count limit is currently imposed.
- Manual submission works while running or paused. Submission is final and
  repeated submit calls are harmless. Remaining time is zero after submission.
- A server worker checks deadlines every second, even without browser requests.
  Every attempt request also enforces expiry before processing changes. If the
  server was stopped, overdue attempts settle on restart, timestamped at their
  actual deadline. No browser timer or open connection is required for submission.
- All changes are serialized in SQLite and require the latest `version` to prevent
  stale tabs or concurrent requests from overwriting saved work. A conflict returns
  HTTP 409: reload state and reconcile the user's intended change.
- Correct answers are excluded from conduction responses. They are available only
  through the owner's analysis endpoint after submission. Current scoring counts
  correct single-option answers; negative/weighted marks are not configured.

## Authentication

Requests use `Authorization: Bearer <token>`. The isolated adapter in `app/auth.py`
verifies HMAC-signed, expiring tokens and checks that the user still exists. There
is no login endpoint in this branch. A trusted developer can mint a local token
for an existing user (with the same `AUTH_TOKEN_SECRET` as the server):

```sh
python -m app.auth 1 --ttl 7200
```

Use a lifetime long enough for the exam and expected pauses, or replace the
`current_user` dependency with your main identity provider. The adapter fails
closed if the secret is absent or shorter than 32 characters. Never mint tokens
in a browser or expose the signing secret.

## API and client integration

All mutation bodies contain `version`, obtained from the latest attempt response.
Start has no body. `question_id` in these routes is the **attempt question ID**
returned by start, not `source_question_id`. Option IDs come from its `options`.

| Method | Route | Body / behavior |
| --- | --- | --- |
| POST | `/api/exams/{exam_id}/attempt` | Start or recover existing attempt |
| GET | `/api/attempts/{id}` | Recover saved state, answers, timer, review flags |
| POST | `/api/attempts/{id}/questions/{question_id}/visit` | `{"version": 1}` |
| PUT | `/api/attempts/{id}/questions/{question_id}/answer` | `{"version": 2, "option_id": 7}` |
| PUT | `/api/attempts/{id}/questions/{question_id}/review` | `{"version": 3, "marked": true}` |
| POST | `/api/attempts/{id}/heartbeat` | `{"version": 4}` |
| POST | `/api/attempts/{id}/pause` | `{"version": 5}` |
| POST | `/api/attempts/{id}/resume` | `{"version": 6}` |
| POST | `/api/attempts/{id}/submit` | `{"version": 7}` |
| GET | `/api/attempts/{id}/analysis` | Post-submission results and activity history |

Render the question palette from `questions`, `visit_count`, `selected_option_id`,
and `marked_for_review`. Call visit whenever the displayed question changes.
Queue mutations (including heartbeats) in the client, always adopting the returned
version. An answer becomes saved when its response succeeds; show pending/error
states until then. On 409 fetch the latest state before retrying an intended edit.
Do not automatically retry with old data after detecting another tab's changes.

Responses include UTC timestamps with `Z`, `server_time`, `remaining_seconds`,
`deadline` while running, and `pause_expires_at` while paused. Render a local
countdown using the server's remaining seconds and elapsed monotonic time; fetch
state at zero, on reconnect, and after focus returns. Send periodic heartbeats
(e.g. every 15 seconds) to checkpoint timing. Closing a tab does not pause an exam;
the user must explicitly pause. Keep the submission summary visible once status
is `submitted`. Saved state survives page reloads and application restarts.

## Analytics and persistence

`app/conduction/models.py` adds `exam_attempts`, `attempt_questions`, and
`attempt_events`. Each question records accumulated milliseconds, visit count,
answer transitions (including first selection and clearing), review state, and
first-visit/latest-answer timestamps. Events record visits, answer changes, pauses,
resumes, review changes, and submission reasons (`manual`, `time_expired`, or
`pause_expired`). Answer event details preserve earlier selections for analysis.

Time spent means wall time with that question active in the attempt. The server
accrues it on navigation, heartbeat, pause, or submission; reads show live elapsed
time without double counting. Pauses are excluded and time is capped at expiry.
It measures the active question, not actual attention: leaving the browser without
pausing continues to count against the current question. Browser focus/visibility
tracking is not implemented. Heartbeats never extend an exam deadline.

The original tables remain in `app/models.py`. SQLite foreign keys are enabled on
every connection. Original source question deletions preserve attempt snapshots;
users/exams with recorded attempts are protected by foreign keys. Migrations
explicitly manage the schema; startup never creates tables. Migration `0002` adds
new tables without changing existing exams.

## Development and verification

```sh
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tests/live_endpoints.py
alembic current
alembic check
```

Tests use an isolated temporary SQLite database, including API authentication,
concurrent writes, exact timeout boundaries, pause/resume timing, answer changes,
snapshots, worker lifecycle, and migration rollback/reapplication. The original
schema and foreign-key tests are retained.

Modules separate schemas, HTTP routing, state transitions, persistence,
transactions, and the deadline worker under `app/conduction/`. Generation has its
own routing, schemas, services, dependencies, and model adapter under `app/generation/`.

`tests/live_endpoints.py` starts a real local Uvicorn server against a temporary
SQLite database and checks all 10 endpoint operations with 30 HTTP assertions.
It also verifies server restart recovery and automatic deadline submission without
client requests. The pause-expiry scenario advances its test database deadline
instead of waiting 15 minutes. It cleans up its server and temporary data.
