import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./prepx.db")
url = make_url(DATABASE_URL)
if url.get_backend_name() != "sqlite":
    raise ValueError("This backend currently supports SQLite only")
# Resolve relative filenames against the project, regardless of working directory.
if url.database and url.database != ":memory:" and not Path(url.database).is_absolute():
    url = url.set(database=str(PROJECT_ROOT / url.database))

engine = create_engine(url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass
