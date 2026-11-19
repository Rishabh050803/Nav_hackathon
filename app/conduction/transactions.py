from contextlib import contextmanager

from sqlalchemy import text

from app.database import SessionLocal
from app.conduction.service import RuleError


@contextmanager
def transaction():
    """SQLite serializes state transitions across requests and worker processes.

RuleError is raised only before a requested transition mutates state. Commit on
that error to preserve an automatic deadline settlement performed beforehand.
All other failures roll back. Never reuse this context for arbitrary writes.
"""
    with SessionLocal() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        try:
            yield session
            session.commit()
        except RuleError:
            session.commit()
            raise
        except BaseException:
            session.rollback()
            raise
