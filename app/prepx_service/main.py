"""Combined entrypoint preserving the parent app and conduction worker."""
from app.main import app
from .router import install

install(app)
