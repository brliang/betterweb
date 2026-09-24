"""The crawl frontier (PLAN.md §6.2): depth limits, enqueueing, priority and cycle planning.

Every function takes the caller's session and leaves committing to it.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.urls import TrackingParams, canonicalize, has_path_segment, host_of
from app.db.web import Domain, Url
from app.enums import FrontierReason
from app.settings import Settings


@dataclass(frozen=True)
class Position:
    """How far a URL is from the pinned seeds (PLAN.md §6.2 "Depth limits")."""

    internal_depth: int
    """Link-clicks within the domain since entering it."""
    external_hops: int
    """Domain jumps since the nearest seed."""

    def follow(self, *, internal: bool) -> "Position":
        """The position of a URL reached by one link (or redirect) from this one."""
        if internal:
            return Position(self.internal_depth + 1, self.external_hops)
        return Position(0, self.external_hops + 1)

    def merge(self, other: "Position") -> "Position":
        """A URL reached by several paths keeps the minimum of each field."""
        return Position(
            min(self.internal_depth, other.internal_depth),
            min(self.external_hops, other.external_hops),
        )

    def within(self, settings: Settings) -> bool:
        return (
            self.internal_depth <= settings.max_internal_depth
            and self.external_hops <= settings.max_external_hops
        )


SEED = Position(0, 0)
"""Pinned homepages. Feed items and sitemap URLs start at depth 0 with their domain's hops."""


@dataclass(frozen=True)
class Candidate:
    url: str
    """Canonical."""
    position: Position


def merge_candidates(candidates: Iterable[Candidate], settings: Settings) -> dict[str, Position]:
    """One position per URL (the merged minimum), dropping those beyond the depth limits and
    account pages (`SKIP_PATH_SEGMENTS`)."""
    merged: dict[str, Position] = {}
    for candidate in candidates:
        existing = merged.get(candidate.url)
        merged[candidate.url] = (
            candidate.position if existing is None else existing.merge(candidate.position)
        )
    skip = frozenset(name.lower() for name in settings.skip_path_segments)
    return {
        url: position
        for url, position in merged.items()
        if position.within(settings) and not has_path_segment(url, skip)
    }


async def ensure_domains(session: AsyncSession, hosts: Iterable[str]) -> dict[str, int]:
    """The ID of each host's web.domains row, inserting missing ones."""
    unique = sorted(set(hosts))
    if not unique:
        return {}
    await session.execute(
        pg_insert(Domain)
        .values([{"host": host} for host in unique])
        .on_conflict_do_nothing(index_elements=[Domain.host])
    )
    rows = await session.execute(sa.select(Domain.host, Domain.id).where(Domain.host.in_(unique)))
    return dict(rows.tuples().all())


async def ensure_urls(session: AsyncSession, urls: Iterable[str]) -> dict[str, int]:
    """The ID of each canonical URL's web.urls row, inserting missing ones (and domains)."""
    unique = sorted(set(urls))
    if not unique:
        return {}
    domains = await ensure_domains(session, (host_of(url) for url in unique))
    await session.execute(
        pg_insert(Url)
        .values([{"url": url, "domain_id": domains[host_of(url)]} for url in unique])
        .on_conflict_do_nothing(index_elements=[Url.url])
    )
    rows = await session.execute(sa.select(Url.url, Url.id).where(Url.url.in_(unique)))
    return dict(rows.tuples().all())


def _domain_prior(domain_id: str, hops: str) -> str:
    """SQL for the domain prior of a URL: its domain's latest score once the scoring stage has
    produced any, and before that (cold start) 1 on pinned domains, 0 elsewhere. Only seeds
    and URLs reached from them without leaving the domain have zero external hops, so
    `hops = 0` identifies pinned domains without reading the user store."""
    return (
        "CASE WHEN EXISTS (SELECT FROM web.domain_scores) "  # noqa: S608 (constant fragments; values are bound)
        "THEN coalesce((SELECT s.score FROM web.domain_scores s "
        f"WHERE s.domain_id = {domain_id}), 0) "
        f"WHEN {hops} = 0 THEN 1 ELSE 0 END"
    )


ENQUEUE = sa.text(
    # Never re-enqueue a URL that was fetched and then dropped (gone, rejected, moved).
    # An existing entry keeps its schedule; its position only ever improves.
    "INSERT INTO web.frontier AS f (url_id, internal_depth, external_hops, priority, reason) "  # noqa: S608 (constant fragments; values are bound)
    "SELECT v.url_id, v.depth, v.hops, "
    f":weight * {_domain_prior('u.domain_id', 'v.hops')}, :reason "
    "FROM unnest(CAST(:url_ids AS bigint[]), CAST(:depths AS smallint[]), "
    "CAST(:hops AS smallint[])) AS v(url_id, depth, hops) "
    "JOIN web.urls u ON u.id = v.url_id "
    "WHERE u.last_fetched_at IS NULL OR EXISTS (SELECT FROM web.frontier e WHERE e.url_id = u.id) "
    "ON CONFLICT (url_id) DO UPDATE SET "
    "internal_depth = least(f.internal_depth, excluded.internal_depth), "
    "external_hops = least(f.external_hops, excluded.external_hops), "
    "priority = greatest(f.priority, excluded.priority) "
    "WHERE excluded.internal_depth < f.internal_depth "
    "OR excluded.external_hops < f.external_hops "
    "RETURNING f.url_id, (xmax = 0) AS inserted"
)


async def enqueue(
    session: AsyncSession, candidates: Iterable[Candidate], settings: Settings
) -> dict[int, bool]:
    """Add candidates to the frontier, or improve the position of ones already there.

    Returns {url_id: True if inserted, False if an existing entry's position improved}; URLs
    that were beyond the depth limits, unchanged, or previously dropped are left out.
    """
    merged = merge_candidates(candidates, settings)
    if not merged:
        return {}
    ids = await ensure_urls(session, merged)
    rows = list(merged.items())
    result = await session.execute(
        ENQUEUE,
        {
            "url_ids": [ids[url] for url, _ in rows],
            "depths": [position.internal_depth for _, position in rows],
            "hops": [position.external_hops for _, position in rows],
            "weight": settings.domain_prior_weight,
            "reason": FrontierReason.NEW.value,
        },
    )
    return dict(result.tuples().all())


ADD_FEEDS = sa.text(
    "UPDATE web.domains SET feed_urls = "
    "ARRAY(SELECT DISTINCT unnest(feed_urls || CAST(:feeds AS text[])) ORDER BY 1) "
    "WHERE host = :host"
)


class SeedError(ValueError):
    """The seed URL or one of its feeds can't be crawled."""


async def add_seed(
    session: AsyncSession, url: str, settings: Settings, feeds: Sequence[str] = ()
) -> int:
    """Enqueue a pinned domain's homepage as a seed and record its feeds; returns the URL ID.

    Called when a user pins a domain (the API role may write `web`); idempotent.
    """
    tracking = TrackingParams(settings.tracking_params)
    homepage = canonicalize(url, tracking)
    if homepage is None:
        raise SeedError(f"not a crawlable URL: {url}")
    feed_urls = []
    for feed in feeds:
        canonical = canonicalize(feed, tracking)
        if canonical is None:
            raise SeedError(f"not a crawlable feed URL: {feed}")
        feed_urls.append(canonical)

    await enqueue(session, [Candidate(homepage, SEED)], settings)
    url_id = (await ensure_urls(session, [homepage]))[homepage]
    if feed_urls:
        await session.execute(ADD_FEEDS, {"host": host_of(homepage), "feeds": feed_urls})
    return url_id


# Planning. Entries are ranked by these keys, first inside each domain and then overall.
_ORDER = {
    # PLAN.md §6.1: re-crawls go by observed change rate.
    FrontierReason.RECRAWL: (
        "u.change_count::float8 / greatest(u.fetch_count, 1) DESC, f.priority DESC, "
        "u.last_fetched_at ASC NULLS FIRST, f.url_id",
        "change_rate DESC, priority DESC, last_fetched_at ASC NULLS FIRST, url_id",
    ),
    FrontierReason.NEW: (
        "f.priority DESC, f.enqueued_at, f.url_id",
        "priority DESC, enqueued_at, url_id",
    ),
}


def _plan_statement(reason: FrontierReason) -> sa.TextClause:
    in_domain, overall = _ORDER[reason]
    # A domain gets at most as many entries as its delay lets us fetch in the time left;
    # a domain with no delay at all is capped only by the limit.
    return sa.text(
        "WITH planned AS ("  # noqa: S608 (constant fragments; values are bound)
        "  SELECT u.domain_id, count(*) AS n FROM web.frontier f "
        "  JOIN web.urls u ON u.id = f.url_id WHERE f.cycle_id = :cycle_id GROUP BY u.domain_id"
        "), candidates AS ("
        "  SELECT f.url_id, f.priority, f.enqueued_at, u.last_fetched_at, "
        "  u.change_count::float8 / greatest(u.fetch_count, 1) AS change_rate, "
        f"  row_number() OVER (PARTITION BY u.domain_id ORDER BY {in_domain}) AS domain_rank, "
        "  coalesce(floor(:window_s / nullif(greatest(:min_delay_s, "
        "  coalesce(d.crawl_delay_s, 0)), 0)) * :concurrency, :limit) "
        "  - coalesce(p.n, 0) AS domain_room "
        "  FROM web.frontier f JOIN web.urls u ON u.id = f.url_id "
        "  JOIN web.domains d ON d.id = u.domain_id "
        "  LEFT JOIN planned p ON p.domain_id = u.domain_id "
        "  WHERE f.cycle_id IS NULL AND f.reason = :reason AND f.next_fetch_at <= :now "
        "  AND NOT (u.domain_id = ANY(CAST(:excluded AS bigint[])))"
        "), chosen AS ("
        "  SELECT url_id FROM candidates WHERE domain_rank <= domain_room "
        f"  ORDER BY {overall} LIMIT :limit"
        ") "
        "UPDATE web.frontier f SET cycle_id = :cycle_id FROM chosen WHERE f.url_id = chosen.url_id"
    )


PLAN_STATEMENTS = {reason: _plan_statement(reason) for reason in FrontierReason}


async def plan(
    session: AsyncSession,
    *,
    cycle_id: int,
    room: int,
    window_s: float,
    settings: Settings,
    now: datetime,
    exclude_domain_ids: Iterable[int] = (),
) -> int:
    """Mark up to `room` due entries as planned for this cycle; returns how many.

    Re-crawls get CYCLE_RECRAWL_SHARE of the room and new URLs the rest (including whatever
    re-crawls leave unused). `window_s` is the time left in the cycle.
    """
    planned = 0
    for reason, limit in (
        (FrontierReason.RECRAWL, math.floor(room * settings.cycle_recrawl_share)),
        (FrontierReason.NEW, None),
    ):
        limit = room - planned if limit is None else limit
        if limit <= 0:
            continue
        result = await session.execute(
            PLAN_STATEMENTS[reason],
            {
                "cycle_id": cycle_id,
                "reason": reason.value,
                "limit": limit,
                "now": now,
                "window_s": window_s,
                "min_delay_s": settings.per_domain_min_delay_s,
                "concurrency": settings.per_domain_concurrency,
                "excluded": sorted(exclude_domain_ids),
            },
        )
        planned += result.rowcount  # type: ignore[attr-defined]  # CursorResult for DML
    return planned


async def release_stale_plans(session: AsyncSession, cycle_id: int) -> int:
    """Unplan entries left over from earlier cycles (a budget or time stop leaves some)."""
    result = await session.execute(
        sa.text(
            "UPDATE web.frontier SET cycle_id = NULL "
            "WHERE cycle_id IS NOT NULL AND cycle_id <> :cycle_id"
        ),
        {"cycle_id": cycle_id},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]  # CursorResult for DML


RECOMPUTE_PRIORITIES = sa.text(
    # OPIC-style estimate (PLAN.md §6.2): each known parent passes on its PageRank divided by
    # its out-degree, plus λ times the domain prior.
    "WITH outdegree AS ("  # noqa: S608 (constant fragments; values are bound)
    "  SELECT src_document_id, count(*) AS n FROM web.links GROUP BY src_document_id"
    "), inflow AS ("
    "  SELECT l.dst_url_id AS url_id, sum(g.pagerank / o.n) AS mass FROM web.links l "
    "  JOIN web.global_scores g ON g.document_id = l.src_document_id "
    "  JOIN outdegree o ON o.src_document_id = l.src_document_id GROUP BY l.dst_url_id"
    ") "
    "UPDATE web.frontier f SET priority = coalesce(i.mass, 0) "
    f"+ :weight * {_domain_prior('u.domain_id', 'f.external_hops')} "
    "FROM web.urls u LEFT JOIN inflow i ON i.url_id = u.id WHERE u.id = f.url_id"
)


async def recompute_priorities(session: AsyncSession, settings: Settings) -> int:
    """Re-estimate every entry's priority; run after the scoring stage (PLAN.md §6.5)."""
    result = await session.execute(RECOMPUTE_PRIORITIES, {"weight": settings.domain_prior_weight})
    return int(result.rowcount)  # type: ignore[attr-defined]  # CursorResult for DML
