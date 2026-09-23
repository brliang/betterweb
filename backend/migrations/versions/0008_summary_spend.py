"""summary spend

Opt-in summaries (PLAN.md §6.8, milestone M9): web.provider_spend.purpose gains `summary`.
usr.summaries already exists (migration 0001); the api role gets it through migration 0002's
default privileges.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

SPEND_PURPOSE_CHECK = "ck_provider_spend_spendpurpose"
SPEND_PURPOSES = ("embed_documents", "embed_topics", "search")


def replace_spend_purposes(purposes: Sequence[str]) -> None:
    # The CHECK that stores the SpendPurpose enum; autogenerate doesn't compare CHECKs.
    op.drop_constraint(op.f(SPEND_PURPOSE_CHECK), "provider_spend", schema="web", type_="check")
    values = ", ".join(f"'{purpose}'" for purpose in purposes)
    op.create_check_constraint(
        op.f(SPEND_PURPOSE_CHECK), "provider_spend", f"purpose IN ({values})", schema="web"
    )


revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    replace_spend_purposes([*SPEND_PURPOSES, "summary"])


def downgrade() -> None:
    op.execute("DELETE FROM web.provider_spend WHERE purpose = 'summary'")
    replace_spend_purposes(SPEND_PURPOSES)
