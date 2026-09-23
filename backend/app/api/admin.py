"""The author's view (PLAN.md §9 "Admin / metrics"): cycle history and the numbers that say
whether the engine works, above all the success metric: likes and clicks on documents from
domains the user did not pin (PLAN.md §1)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AdminUser, DbSession, Now, SettingsDep
from app.db.base import JSONObject
from app.db.usr import Event, Recommendation
from app.db.web import CrawlCycle, Document, DocumentEmbedding, FrontierEntry, ProviderSpend
from app.enums import CycleStatus, DocumentType, EventKind, FrontierReason, Slice, Surface
from app.pins import pinned_domains
from app.spend import month_spend_usd

router = APIRouter(prefix="/admin", tags=["admin"])


class CycleSummary(BaseModel):
    id: int
    status: CycleStatus
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    page_budget: int
    pages_fetched: int
    spend_usd: float
    stats: JSONObject
    """Per-stage counts, errors and timings, as each stage recorded them."""


class TypeCount(BaseModel):
    type: DocumentType
    documents: int


class DailyMetrics(BaseModel):
    day: date
    """In the admin's timezone."""
    impressions: int
    clicks: int
    likes: int
    hides: int
    discoveries: int
    """Clicks and likes on documents from domains the user did not pin: the success metric."""


class SliceMetrics(BaseModel):
    slice: Slice
    impressions: int
    """Feed items of this slice seen at least once."""
    clicks: int
    """Feed items of this slice clicked at least once."""
    click_through: float | None


class Metrics(BaseModel):
    days: int
    documents: int
    embedded: int
    """Documents with an embedding from the configured model."""
    corpus: list[TypeCount]
    frontier_new: int
    frontier_recrawl: int
    frontier_due: int
    month_spend_usd: float
    spend_cap_usd: float
    daily: list[DailyMetrics]
    hide_rate: float | None
    """Hides per impression over the window."""
    slices: list[SliceMetrics]
    """Exploration click-through against the main slice, over the window."""


@router.get("/cycles")
async def cycles(_: AdminUser, session: DbSession, settings: SettingsDep) -> list[CycleSummary]:
    """The most recent crawl cycles (ADMIN_CYCLES_SHOWN), newest first."""
    rows = list(
        await session.scalars(
            sa.select(CrawlCycle).order_by(CrawlCycle.id.desc()).limit(settings.admin_cycles_shown)
        )
    )
    spend = dict(
        (
            await session.execute(
                sa.select(ProviderSpend.cycle_id, sa.func.sum(ProviderSpend.cost_usd))
                .where(ProviderSpend.cycle_id.in_([cycle.id for cycle in rows]))
                .group_by(ProviderSpend.cycle_id)
            )
        )
        .tuples()
        .all()
    )
    return [
        CycleSummary(
            id=cycle.id,
            status=cycle.status,
            started_at=cycle.started_at,
            finished_at=cycle.finished_at,
            duration_s=(cycle.finished_at - cycle.started_at).total_seconds()
            if cycle.finished_at
            else None,
            page_budget=cycle.page_budget,
            pages_fetched=cycle.pages_fetched,
            spend_usd=float(spend.get(cycle.id) or 0.0),
            stats=cycle.stats,
        )
        for cycle in rows
    ]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@router.get("/metrics")
async def metrics(user: AdminUser, session: DbSession, settings: SettingsDep, now: Now) -> Metrics:
    days = settings.metrics_days
    local_day = sa.func.date(sa.func.timezone(user.timezone, Event.created_at))
    first_day = now.astimezone(ZoneInfo(user.timezone)).date() - timedelta(days=days - 1)
    in_window = local_day >= first_day

    counts: dict[tuple[date, EventKind], int] = {
        (day, kind): count
        for day, kind, count in await session.execute(
            sa.select(local_day, Event.kind, sa.func.count())
            .where(in_window)
            .group_by(local_day, Event.kind)
        )
    }
    pinned = pinned_domains().subquery()
    discoveries = dict(
        (
            await session.execute(
                sa.select(local_day, sa.func.count())
                .join(Document, Document.id == Event.document_id)
                .where(
                    in_window,
                    Event.kind.in_([EventKind.CLICK, EventKind.LIKE]),
                    ~sa.exists().where(
                        pinned.c.user_id == Event.user_id, pinned.c.id == Document.domain_id
                    ),
                )
                .group_by(local_day)
            )
        )
        .tuples()
        .all()
    )
    daily = [
        DailyMetrics(
            day=day,
            impressions=counts.get((day, EventKind.IMPRESSION), 0),
            clicks=counts.get((day, EventKind.CLICK), 0),
            likes=counts.get((day, EventKind.LIKE), 0),
            hides=counts.get((day, EventKind.HIDE), 0),
            discoveries=discoveries.get(day, 0),
        )
        for day in (first_day + timedelta(days=offset) for offset in range(days))
    ]

    seen = sa.func.count(sa.distinct(Event.recommendation_id))
    slices = [
        SliceMetrics(
            slice=kind,
            impressions=impressions,
            clicks=clicks,
            click_through=_ratio(clicks, impressions),
        )
        for kind, impressions, clicks in await session.execute(
            sa.select(
                Recommendation.slice,
                seen.filter(Event.kind == EventKind.IMPRESSION),
                seen.filter(Event.kind == EventKind.CLICK),
            )
            .join(Recommendation, Recommendation.id == Event.recommendation_id)
            .where(in_window, Recommendation.surface == Surface.FEED)
            .group_by(Recommendation.slice)
            .order_by(Recommendation.slice)
        )
    ]

    frontier = dict(
        (
            await session.execute(
                sa.select(FrontierEntry.reason, sa.func.count()).group_by(FrontierEntry.reason)
            )
        )
        .tuples()
        .all()
    )
    corpus = [
        TypeCount(type=kind, documents=count)
        for kind, count in await session.execute(
            sa.select(Document.type, sa.func.count())
            .group_by(Document.type)
            .order_by(Document.type)
        )
    ]
    return Metrics(
        days=days,
        documents=sum(entry.documents for entry in corpus),
        embedded=await session.scalar(
            sa.select(sa.func.count())
            .select_from(DocumentEmbedding)
            .where(DocumentEmbedding.model == settings.embedding_model)
        )
        or 0,
        corpus=corpus,
        frontier_new=frontier.get(FrontierReason.NEW, 0),
        frontier_recrawl=frontier.get(FrontierReason.RECRAWL, 0),
        frontier_due=await session.scalar(
            sa.select(sa.func.count())
            .select_from(FrontierEntry)
            .where(FrontierEntry.next_fetch_at <= now)
        )
        or 0,
        month_spend_usd=await month_spend_usd(session, now),
        spend_cap_usd=settings.provider_monthly_spend_cap_usd,
        daily=daily,
        hide_rate=_ratio(sum(day.hides for day in daily), sum(day.impressions for day in daily)),
        slices=slices,
    )
