from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase

WEB = "web"
"""Shared web graph: public information only, never references `usr` (PLAN.md §4)."""
USR = "usr"
"""User store: everything keyed by user_id; deleting a user cascades from usr.users."""

JSONObject = dict[str, object]
Embedding = list[float]

EMBEDDING_DIMENSIONS = 1024
"""Vector size of every embedding column (PLAN.md §6.4). Changing it needs a migration and
re-embedding everything; providers truncate longer vectors to this size."""


class Base(DeclarativeBase):
    # Deterministic constraint names, so Alembic can drop and alter them.
    metadata = sa.MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {  # noqa: RUF012 (SQLAlchemy reads this class attribute as-is)
        str: sa.Text,  # Postgres: text, not varchar
        datetime: TIMESTAMP(timezone=True),
        JSONObject: JSONB,
        # M5 adds the HNSW indexes.
        Embedding: VECTOR(EMBEDDING_DIMENSIONS),
        # Enums are stored as their values in text columns with a CHECK constraint.
        Enum: sa.Enum(
            Enum,
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
            validate_strings=True,
        ),
    }
