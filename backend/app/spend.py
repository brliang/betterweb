"""The provider spend ledger, web.provider_spend (PLAN.md §6.4).

The monthly cap covers the calendar month in UTC. Callers open a meter sized to what is left of
it, pass the meter to the provider, and record its charges in the transaction that stores what
they paid for.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import ProviderSpend
from app.enums import SpendPurpose
from app.providers.spend import SpendMeter
from app.settings import Settings


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_spend_usd(session: AsyncSession, now: datetime | None = None) -> float:
    """Provider spend recorded since the start of this calendar month (UTC)."""
    since = month_start(now or datetime.now(UTC))
    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(ProviderSpend.cost_usd), 0.0)).where(
            ProviderSpend.created_at >= since
        )
    )
    return float(total or 0.0)


async def open_meter(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> SpendMeter:
    """A meter holding what is left of this month's PROVIDER_MONTHLY_SPEND_CAP_USD."""
    spent = await month_spend_usd(session, now)
    return SpendMeter(max(0.0, settings.provider_monthly_spend_cap_usd - spent))


async def record_spend(
    session: AsyncSession,
    meter: SpendMeter,
    purpose: SpendPurpose,
    cycle_id: int | None = None,
) -> float:
    """Add the meter's pending charges to the ledger, one row per model; returns their total.

    The caller commits, together with whatever the charges paid for.
    """
    rows: dict[tuple[str, bool], ProviderSpend] = {}
    total = 0.0
    for charge in meter.take():
        key = (charge.model, charge.estimated)
        row = rows.get(key)
        if row is None:
            row = rows[key] = ProviderSpend(
                cycle_id=cycle_id,
                purpose=purpose,
                model=charge.model,
                requests=0,
                tokens=0,
                cost_usd=0.0,
                estimated=charge.estimated,
            )
        row.requests += 1
        row.tokens += charge.tokens
        row.cost_usd += charge.cost_usd
        total += charge.cost_usd
    session.add_all(rows.values())
    return total
