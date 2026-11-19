"""Configure the database before importing any application module."""
import os
from pathlib import Path
import tempfile

from alembic import command
from alembic.config import Config

_temp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite:///{_temp.name}/test.db'
os.environ['AUTH_TOKEN_SECRET'] = 'test-only-secret-that-is-at-least-32-characters'
CONFIG = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))


def migrate():
    command.upgrade(CONFIG, 'head')
