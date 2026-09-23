"""embedding dimensions

Fixes every embedding column at 1024 dimensions now that the model is chosen (Qwen3-Embedding-8B,
truncated; PLAN.md §14 Q3), and records which model embedded each topic. Autogenerate doesn't
compare vector sizes, so the column type changes are hand-written.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23 08:43:42.254795
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIMENSIONS = 1024
EMBEDDING_COLUMNS = [
    ("web", "document_embeddings", "vector", False),
    ("web", "topics", "embedding", True),
    ("usr", "user_profile_vectors", "vector", False),
]


def upgrade() -> None:
    # Fails if a column already holds vectors of another size; none exist before this revision.
    for schema, table, column, nullable in EMBEDDING_COLUMNS:
        op.alter_column(
            table,
            column,
            schema=schema,
            type_=VECTOR(DIMENSIONS),
            existing_type=VECTOR(),
            existing_nullable=nullable,
        )
    op.add_column("topics", sa.Column("embedding_model", sa.Text(), nullable=True), schema="web")


def downgrade() -> None:
    op.drop_column("topics", "embedding_model", schema="web")
    for schema, table, column, nullable in EMBEDDING_COLUMNS:
        op.alter_column(
            table,
            column,
            schema=schema,
            type_=VECTOR(),
            existing_type=VECTOR(DIMENSIONS),
            existing_nullable=nullable,
        )
