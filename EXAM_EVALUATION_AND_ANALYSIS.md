# Exam evaluation and analysis

The evaluation module in `app/exam_evaluation/` grades the finalized answers against the attempt's saved options. The analysis module in `app/exam_analysis/` uses those grades and the server's recorded per-question focus time to build the performance report.

## Database

Both modules read the existing conduction tables `exam_attempts`, `attempt_questions`, and `attempt_events`. They require no new migration. Generated exams use the ordered `exams.paper` snapshot to start the attempt, including model-generated items. Older exams use their linked bank questions. Previous-year items keep their bank ID as `source_question_id`, while generated items use the negative of their position as a paper-local source ID. Use the question and option IDs from the attempt when saving answers.

## API

Use a bearer token for the attempt endpoints. For local testing, set the same `AUTH_TOKEN_SECRET` of at least 32 characters in the server and client terminals, then mint a token for the demo user with `python -m app.auth 1`. The endpoints are available in Swagger at `/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/exams/{exam_id}/attempt` | Start or retrieve the owned attempt. |
| PUT | `/api/attempts/{attempt_id}/questions/{question_id}/answer` | Save an option ID with the current attempt version. Send null to clear an answer. |
| POST | `/api/attempts/{attempt_id}/questions/{question_id}/visit` | Move focus to the question using the current version so the server can track time. |
| POST | `/api/attempts/{attempt_id}/submit` | Finalize the attempt with the current version. |
| GET | `/api/attempts/{attempt_id}/evaluation` | Get the score and counts for correct, incorrect and unattempted questions. |
| GET | `/api/attempts/{attempt_id}/analysis` | Get the evaluation plus detailed feedback and timing. |

Both reports are available after manual or automatic submission. An owned attempt still in progress returns `409`. A missing attempt or one owned by another user returns `404`.

## How to read the report

- Each question has an explicit `status` of `correct`, `incorrect`, or `unattempted`. The report includes the selected option, correct options, time spent in milliseconds, visits, answer changes, and review flags.
- `score_percent` divides the correct count by all questions. `accuracy_percent` divides it by answered questions and is null when there are zero answers.
- Topics are grouped case-insensitively. A topic is strong at 80% accuracy or above, weak below 50%, and developing in between. Topics with no answers or no topic label have insufficient data. These classifications describe the current attempt, and the breakdown includes the underlying counts.
- The report includes difficulty breakdowns, mean time spent across all questions, and the number of unvisited questions. Time is measured while the question is active, excludes pauses, and is capped at the exam deadline. A question can be visited without being answered.
