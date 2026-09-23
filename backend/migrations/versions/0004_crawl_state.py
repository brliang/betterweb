"""crawl state

State the fetcher needs (PLAN.md §6.1-6.2, milestone M3):
- web.raw_pages: fetched bodies waiting for the extract stage.
- web.frontier.cycle_id: the cycle whose plan includes the entry, so a killed cycle resumes.
- web.frontier.failures: transient failures in a row, for the retry backoff.
- web.urls.redirect_to_url_id: where a URL redirected, for the dedup stage.

The crawl, score and api roles get web.raw_pages through migration 0002's default privileges.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23 09:18:08.036517
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_pages",
        sa.Column("url_id", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("charset", sa.Text(), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["web.crawl_cycles.id"],
            name=op.f("fk_raw_pages_cycle_id_crawl_cycles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["url_id"], ["web.urls.id"], name=op.f("fk_raw_pages_url_id_urls"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("url_id", name=op.f("pk_raw_pages")),
        schema="web",
    )
    op.create_index(
        op.f("ix_raw_pages_cycle_id"), "raw_pages", ["cycle_id"], unique=False, schema="web"
    )
    op.add_column("frontier", sa.Column("cycle_id", sa.BigInteger(), nullable=True), schema="web")
    op.add_column(
        "frontier",
        sa.Column("failures", sa.SmallInteger(), server_default="0", nullable=False),
        schema="web",
    )
    op.create_index(
        op.f("ix_frontier_cycle_id"), "frontier", ["cycle_id"], unique=False, schema="web"
    )
    op.create_foreign_key(
        op.f("fk_frontier_cycle_id_crawl_cycles"),
        "frontier",
        "crawl_cycles",
        ["cycle_id"],
        ["id"],
        source_schema="web",
        referent_schema="web",
        ondelete="SET NULL",
    )
    op.add_column(
        "urls", sa.Column("redirect_to_url_id", sa.BigInteger(), nullable=True), schema="web"
    )
    op.create_index(
        op.f("ix_urls_redirect_to_url_id"),
        "urls",
        ["redirect_to_url_id"],
        unique=False,
        schema="web",
    )
    op.create_foreign_key(
        op.f("fk_urls_redirect_to_url_id_urls"),
        "urls",
        "urls",
        ["redirect_to_url_id"],
        ["id"],
        source_schema="web",
        referent_schema="web",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_urls_redirect_to_url_id_urls"), "urls", schema="web", type_="foreignkey"
    )
    op.drop_index(op.f("ix_urls_redirect_to_url_id"), table_name="urls", schema="web")
    op.drop_column("urls", "redirect_to_url_id", schema="web")
    op.drop_constraint(
        op.f("fk_frontier_cycle_id_crawl_cycles"), "frontier", schema="web", type_="foreignkey"
    )
    op.drop_index(op.f("ix_frontier_cycle_id"), table_name="frontier", schema="web")
    op.drop_column("frontier", "failures", schema="web")
    op.drop_column("frontier", "cycle_id", schema="web")
    op.drop_index(op.f("ix_raw_pages_cycle_id"), table_name="raw_pages", schema="web")
    op.drop_table("raw_pages", schema="web")
