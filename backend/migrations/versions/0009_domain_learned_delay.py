"""domain learned delay

web.domains.learned_delay_s: the delay between requests a domain's gate adapted to by the end
of the last fetch round, so the next round or cycle starts there and plans by it (adaptive
crawl pace).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-25 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("domains", sa.Column("learned_delay_s", sa.Float(), nullable=True), schema="web")


def downgrade() -> None:
    op.drop_column("domains", "learned_delay_s", schema="web")
