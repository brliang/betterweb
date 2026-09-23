"""embeddings and provider spend

The embed stage (PLAN.md §6.4, milestone M5):
- web.provider_spend: a ledger of model provider spend, summed for the monthly cap.
- web.document_embeddings.input_hash / document_updated_at: which text and which version of
  the document a vector covers, so unchanged documents cost no request. The table has had no
  writer before this milestone, so the NOT NULL columns need no default.
- An HNSW index for cosine nearest-neighbour search over document embeddings.

The crawl, score and api roles get web.provider_spend through migration 0002's default
privileges.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23 10:37:51.827331
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_spend",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cycle_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "purpose",
            sa.Enum(
                "embed_documents",
                "embed_topics",
                name="spendpurpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["web.crawl_cycles.id"],
            name=op.f("fk_provider_spend_cycle_id_crawl_cycles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_spend")),
        schema="web",
    )
    op.create_index(
        op.f("ix_provider_spend_created_at"),
        "provider_spend",
        ["created_at"],
        unique=False,
        schema="web",
    )
    op.create_index(
        op.f("ix_provider_spend_cycle_id"),
        "provider_spend",
        ["cycle_id"],
        unique=False,
        schema="web",
    )
    op.add_column(
        "document_embeddings", sa.Column("input_hash", sa.Text(), nullable=False), schema="web"
    )
    op.add_column(
        "document_embeddings",
        sa.Column("document_updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="web",
    )
    op.create_index(
        "ix_document_embeddings_vector",
        "document_embeddings",
        ["vector"],
        unique=False,
        schema="web",
        postgresql_using="hnsw",
        postgresql_ops={"vector": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_embeddings_vector",
        table_name="document_embeddings",
        schema="web",
        postgresql_using="hnsw",
        postgresql_ops={"vector": "vector_cosine_ops"},
    )
    op.drop_column("document_embeddings", "document_updated_at", schema="web")
    op.drop_column("document_embeddings", "input_hash", schema="web")
    op.drop_index(op.f("ix_provider_spend_cycle_id"), table_name="provider_spend", schema="web")
    op.drop_index(op.f("ix_provider_spend_created_at"), table_name="provider_spend", schema="web")
    op.drop_table("provider_spend", schema="web")
