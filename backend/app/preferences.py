"""Writing a user's interests, pins and ranking settings (PLAN.md §7), for the survey, the
settings page and the pins endpoints. Every function leaves committing to the caller.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.frontier import add_seed, ensure_domains
from app.crawl.urls import TrackingParams, canonicalize, host_of, origin_of, same_site
from app.db.usr import Pin, UserInterest, UserSettings
from app.db.web import Topic
from app.enums import DocumentType, InterestLevel, InterestSource, PinSource, RankingPreset
from app.rank.context import default_split
from app.settings import Settings
from app.suggested_sources import SuggestedSource, load_suggested_sources


class PreferenceError(ValueError):
    """An answer can't be stored: an unknown topic, an uncrawlable site, a share not offered."""


@lru_cache
def suggested_sources() -> tuple[SuggestedSource, ...]:
    return tuple(load_suggested_sources())


def suggested_source_for(host: str) -> SuggestedSource | None:
    return next((s for s in suggested_sources() if same_site(s.host, host)), None)


@dataclass(frozen=True)
class Site:
    host: str
    homepage: str
    """Canonical homepage URL; pinning enqueues it as a seed."""
    entered: str
    """The canonical URL as entered, if it isn't the homepage; also enqueued."""


def parse_site(text: str, settings: Settings) -> Site:
    """A domain ("example.com") or URL a user entered, as the site to pin."""
    text = text.strip()
    url = text if "://" in text else f"https://{text}"
    canonical = canonicalize(url, TrackingParams(settings.tracking_params))
    if not text or canonical is None or "." not in host_of(canonical).strip("[]"):
        raise PreferenceError(f"not a website: {text!r}")
    return Site(host=host_of(canonical), homepage=f"{origin_of(canonical)}/", entered=canonical)


def interest_level(weight: float, settings: Settings) -> InterestLevel:
    very = settings.interest_level_weights[InterestLevel.VERY_INTERESTED]
    return InterestLevel.VERY_INTERESTED if weight >= very else InterestLevel.INTERESTED


async def replace_interests(
    session: AsyncSession,
    user_id: uuid.UUID,
    interests: Sequence[tuple[int, InterestLevel]],
    settings: Settings,
) -> None:
    topic_ids = {topic_id for topic_id, _ in interests}
    known = set(await session.scalars(sa.select(Topic.id).where(Topic.id.in_(topic_ids))))
    if unknown := sorted(topic_ids - known):
        raise PreferenceError(f"unknown topics: {unknown}")
    await session.execute(sa.delete(UserInterest).where(UserInterest.user_id == user_id))
    levels = dict(interests)
    session.add_all(
        UserInterest(
            user_id=user_id,
            topic_id=topic_id,
            weight=settings.interest_level_weights[level],
            source=InterestSource.SURVEY,
        )
        for topic_id, level in sorted(levels.items())
    )
    await session.flush()


async def save_settings(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    preset: RankingPreset,
    exploration_pct: float,
    content_types: Sequence[DocumentType],
    summaries_opt_in: bool | None,
    settings: Settings,
) -> None:
    """Upsert usr.user_settings; `summaries_opt_in=None` keeps the current choice (the survey
    doesn't ask it, and it defaults to off)."""
    if not any(abs(exploration_pct - choice) < 1e-9 for choice in settings.exploration_choices):
        raise PreferenceError(
            f"exploration share {exploration_pct} is not one of {settings.exploration_choices}"
        )
    values: dict[str, object] = {
        "preset": preset,
        "weights": settings.ranking_presets[preset].model_dump(),
        "exploration_pct": exploration_pct,
        "exploration_split": {kind.value: share for kind, share in default_split(settings).items()},
        "content_types": sorted({kind.value for kind in content_types}),
    }
    if summaries_opt_in is not None:
        values["summaries_opt_in"] = summaries_opt_in
    statement = pg_insert(UserSettings).values(user_id=user_id, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[UserSettings.user_id],
            set_={**values, "updated_at": sa.func.now()},
        )
    )


async def pin_site(
    session: AsyncSession, user_id: uuid.UUID, site: Site, settings: Settings
) -> int:
    """Pin the site's domain and enqueue its homepage (and the URL entered, and a suggested
    source's feed) as crawl seeds (PLAN.md §7). Idempotent; returns the domain ID."""
    suggested = suggested_source_for(site.host)
    feeds = [str(suggested.feed)] if suggested is not None else []
    await add_seed(session, site.homepage, settings, feeds)
    if site.entered != site.homepage:
        await add_seed(session, site.entered, settings)
    domain_id = (await ensure_domains(session, [site.host]))[site.host]
    await session.execute(
        pg_insert(Pin)
        .values(
            user_id=user_id,
            domain_id=domain_id,
            source=PinSource.SUGGESTED if suggested is not None else PinSource.SURVEY,
        )
        .on_conflict_do_nothing(index_elements=[Pin.user_id, Pin.domain_id])
    )
    return domain_id


async def replace_pins(
    session: AsyncSession, user_id: uuid.UUID, sites: Sequence[Site], settings: Settings
) -> None:
    kept = [await pin_site(session, user_id, site, settings) for site in sites]
    await session.execute(sa.delete(Pin).where(Pin.user_id == user_id, Pin.domain_id.not_in(kept)))
