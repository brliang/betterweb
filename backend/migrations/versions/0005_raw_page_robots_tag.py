"""raw page robots tag

web.raw_pages.robots_tag: the page's X-Robots-Tag headers, so the extract stage can honor
`noindex` and `nofollow` sent as headers (PDFs can't carry a robots meta tag) (milestone M4).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23 10:17:55.463389
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("raw_pages", sa.Column("robots_tag", sa.Text(), nullable=True), schema="web")


def downgrade() -> None:
    op.drop_column("raw_pages", "robots_tag", schema="web")
