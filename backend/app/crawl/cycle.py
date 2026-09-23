"""Crawl cycle bookkeeping (PLAN.md §6.1): one row in web.crawl_cycles per nightly run."""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import CrawlCycle
from app.enums import CycleStatus
from app.settings import Settings

CYCLE_LOCK_KEY = 0x6B7C_0001
"""Postgres advisory lock held while a cycle runs, so two workers never run one at once."""


async def start_or_resume_cycle(session: AsyncSession, settings: Settings) -> CrawlCycle:
    """The running cycle if one was interrupted (resume it), else a new one."""
    cycle = await session.scalar(
        sa.select(CrawlCycle)
        .where(CrawlCycle.status == CycleStatus.RUNNING)
        .order_by(CrawlCycle.id.desc())
        .limit(1)
    )
    if cycle is None:
        cycle = CrawlCycle(page_budget=settings.cycle_page_budget, stats={})
        session.add(cycle)
        await session.flush()
        await session.refresh(cycle)
    await session.commit()
    return cycle


async def finish_cycle(session: AsyncSession, cycle: CrawlCycle) -> None:
    cycle.status = CycleStatus.SUCCEEDED
    cycle.finished_at = datetime.now(UTC)
    await session.commit()
