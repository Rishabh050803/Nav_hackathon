# PrepX PDF ingestion module

This folder is integrated into the main backend at `app/prepx_service`. Run commands from the **repository root**, not from this folder.

## Shared database and authentication

- Entities come directly from `app.models`: `User`, `PdfUpload`, `Question`, `Option`, `Exam`, and `exam_questions`.
- Sessions come from `app.database.SessionLocal`. There is no separate engine, SQLite path, declarative Base, model copy, or migration chain.
- Ingestion uses `app.auth.current_user` and the existing bearer tokens. Upload forms cannot choose their owner via `user_id`.
- The repository's existing migrations are the only schema authority. No tables or users are created at startup.
- Existing exam ownership is checked before extraction and again inside the write transaction. Slow OCR/Groq work runs without holding a SQLite read/write transaction. Final writes use `BEGIN IMMEDIATE`, consistent with the main backend's SQLite serialization.

Removed standalone artifacts: duplicate models/database setup, demo-user CLI, attach-db helper, service Alembic configuration and migrations, extra metadata entity, independent shared API key, and standalone dependency lock. Service requirements include the main requirements instead of repeating them.

## Setup and startup

Use the main backend's virtual environment. Python 3.11/3.12 is recommended for optional ML model compatibility.

```powershell
python -m pip install -r app/prepx_service/requirements-dev.txt
Copy-Item app/prepx_service/.env.example app/prepx_service/.env
```

Set `GROQ_API_KEY` in `app/prepx_service/.env`. This file configures OCR/LLM settings only. Export `DATABASE_URL` and `AUTH_TOKEN_SECRET` in the process environment as the main backend requires; its database module does not automatically load dotenv files. Relative SQLite paths resolve against the main repository root.

For a fresh database, apply the **main** migrations:

```powershell
python -m alembic upgrade head
```

If the existing database is already migrated, no schema changes are required for ingestion. Do not run old service migrations or `attach-db`. This module no longer depends on `question_extraction_metadata`. If that table already exists from an earlier standalone installation, it is left untouched and unused; no data is deleted automatically.

Start the combined app using this entrypoint:

```powershell
python -m uvicorn app.prepx_service.main:app --reload
```

This entrypoint imports the existing `app.main.app`, preserving its conduction routes, error handler, and lifespan/deadline worker, then registers the ingestion router once. All production changes are confined to this folder. The unchanged `uvicorn app.main:app` command still starts only the original backend. The explicit `install(app)` hook in `app/prepx_service/router.py` can be used if you later choose to edit the parent entrypoint.

Open `/docs` to see conduction and ingestion together. Use one worker for the ingestion concurrency limit: one active batch per process, with synchronous processing suitable for the small hackathon workload.

## Request and persistence

Authenticate with the same `Authorization: Bearer <token>` used by conduction. Existing local token creation still works: `python -m app.auth USER_ID --ttl 7200` with the configured signing secret.

`POST /api/v1/ingestions` accepts multipart fields:

| Field | Meaning |
| --- | --- |
| `files` | Repeated PDF uploads; default five files, 1-10 pages and 20 MB each |
| `exam_id` | Optional existing exam belonging to the authenticated user |
| `examname` | Optional name for newly created exams; existing exam titles are preserved |
| `languages` | Optional comma-separated codes such as `en,hi` |
| `ocr_backend` | `tesseract` by default, optional `easyocr` |
| `force_ocr` | Force OCR even if a native text layer exists |

Example (set the token locally):

```powershell
curl.exe http://127.0.0.1:8000/api/v1/ingestions -H "Authorization: Bearer $env:PREPX_TOKEN" -F "languages=en,hi" -F "files=@C:/papers/exam.pdf"
```

Without `exam_id`, each PDF creates an upload and an exam owned by the authenticated user. With `exam_id`, questions append to the existing exam. Question count and important topics are updated. Repeat uploads create new records; automatic deduplication is not implemented.

| Output | Existing database mapping |
| --- | --- |
| Question text, topic, difficulty | `questions.question_text`, `topic`, `difficulty` |
| Source page contains raster image | `questions.has_image` (page-level approximation) |
| Choices and correct choices | `options.label`, `text`, `is_correct` |
| Filename and owner | `pdf_uploads.original_filename`, `user_id` |
| Exam name and associations | `exams.title`, `exam_questions` |

Language, source pages, original question number, generated-answer provenance, raw difficulty score, and warnings are returned in JSON **only**. The current main schema has no columns for these; no extra metadata table is added. Correct-answer text is derived from the correct options, rather than duplicated in a separate column. This means answer provenance and language cannot be recovered from the database later without a future approved schema change. Uploaded source files are not stored.

The response remains `{"documents": [{"filename": ..., "pdf_upload_id": ..., "exam_id": ..., "pages": ..., "warnings": [...], "questions": [...]}]}`. Each question contains `question_id`, `original_question_number`, `question_text`, `options`, `topic`, `level`, `examname`, `languages`, `source_pages`, `correct_labels`, `correct_answer_text`, `answer_source`, `difficulty_score`, `has_image`, and `warnings`.

Supplied PDF answers are preserved. Groq is asked to solve only missing answers. Generated labels must match existing choices. Unresolved answers have `correct_answer_text=null`, `correct_labels=[]`, and all `options.is_correct=NULL`. Multiple correct choices can be stored, but the existing conduction scoring currently handles single-option answers only.

Difficulty is estimated by the LLM, then assigned easy/medium/difficult using midrank percentiles within each PDF. Equal scores stay together; a single question receives medium. Historical questions in an existing exam are not regraded.

All PDFs are validated before extraction and all extraction/answer work finishes before database mutation. The entire batch commits together or rolls back on failure. Exam duration is not inferred by this parser: new exams retain the model's zero timing defaults. Conduction requires a positive `total_time_seconds` or `max_time` before starting an attempt; appending to an already configured exam preserves its timing and attempt snapshots.

## OCR and Groq dependencies

Install Tesseract separately with English and the Indian-language packs you need. Set `TESSERACT_CMD` in the service `.env` if it is not on PATH. `GET /api/v1/languages` (bearer-authenticated) reports installed Tesseract packs and available backend mappings.

The registry is English plus the 22 scheduled Indian languages. Tesseract mappings cover en, as, bn, gu, hi, kn, ml, mr, ne, or, pa, sa, sd, ta, te, ur. Optional EasyOCR adds Goan Konkani (`kok`) and Maithili (`mai`) among its mappings. Bodo, Dogri, Kashmiri, Manipuri, and Santali have no direct models configured in these backends; script similarity is not treated as verified language support. Other language codes are rejected.

For optional EasyOCR and local equation recognition:

```powershell
python -m pip install -r app/prepx_service/requirements-ocr-extra.txt
```

Use explicit compatible language hints with EasyOCR. Enable `MATH_OCR_ENABLED=true` for Pix2Text formula detection/LaTeX recognition, with Tesseract or EasyOCR reading the surrounding text. The optional models may download large weights on first use and need compatible ML dependencies. Basic Tesseract mode warns that complex image equations may be inaccurate.

Native text is used when available; image-bearing pages use OCR to avoid missing embedded questions. Bilingual versions stay together in one question. Groq structures text, matches answer keys within the PDF, estimates topic/difficulty, and solves missing answers. Free-tier quotas apply; the configured Groq model must support strict structured output. Layout reconstruction, language recognition, equation OCR, and generated answers remain fallible and need representative Indian PYQ validation. No live inference accuracy has been established by the automated tests.

## Verification

From the repository root:

```powershell
python -m pytest app/prepx_service/tests -q
python -m unittest discover -s tests -v
```

The ingestion tests use the **main migration chain** against isolated temporary SQLite databases. They check model/schema equivalence, batch rollback, main bearer authentication, ignoring spoofed form user IDs, ownership, option correctness, native/mixed PDF extraction, mocked equation recognition, and importing parsed questions through the actual conduction API. OCR and Groq inference are mocked. The unchanged backend database/conduction tests are also run for regression coverage.
