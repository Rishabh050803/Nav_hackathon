# PrepX FastAPI backend

Database-only Python backend inspired by the original Java project in
`../PrepX-exam_creation/backend/prepx`. This directory is the standalone PrepX project.
Python 3.10+ is required. No application API endpoints or business services are implemented.

## Setup

Run from this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

SQLite defaults to `prepx.db` in this directory. To use another file, export
`DATABASE_URL=sqlite:////absolute/path/to/prepx.db` before running commands.
Relative database paths resolve against this project directory. `.env.example`
is documentation; `.env` files are not automatically loaded.

## Database

`app/models.py` contains `User`, `PdfUpload`, `Question`, `Option`, `Exam`, and
`exam_questions` (a many-to-many join table with a composite primary key).
Models retain the original entity fields and table names, using snake_case column
names. `AuthProviderType` supports GOOGLE, GITHUB, EMAIL with a database check.
Questions and option text use SQLite TEXT. New uploads/exams default to UTC
creation times. `max_time` is minutes; other time fields explicitly use seconds.

Foreign keys are enabled for every application/migration connection. Users and
uploads referenced by other records cannot be deleted. Deleting a question
removes its options and exam associations; deleting an exam removes only its
associations, preserving shared questions. Passwords are intended to hold hashes.

Migrations create the schema explicitly; app startup does not create tables.
This creates a fresh SQLite database and does not transfer existing Java data.

```sh
alembic current
alembic check
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
# Destructive: removes tables and their data in the initial migration.
alembic downgrade base
```

## Verification

```sh
python -m unittest discover -s tests -v
```

The database test uses a temporary SQLite file and checks migration round trips,
foreign keys, username uniqueness, and shared-question deletion behavior.
