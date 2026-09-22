"""Imports every model module, so `Base.metadata` holds all tables (for Alembic and tests)."""

from app.db import usr, web
from app.db.base import Base

__all__ = ["Base", "usr", "web"]
