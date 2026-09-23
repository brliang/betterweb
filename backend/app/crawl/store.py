"""Database access for the fetch stage.

One session, used by one operation at a time, and every operation commits on its own: a
killed stage loses at most the requests in flight, and a re-run resumes from the entries still
marked with the cycle (PLAN.md §6.1: stages are idempotent and resumable).
"""

import asyncio
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl import frontier
from app.crawl.frontier import Candidate, Position
from app.crawl.http import FetchResult, Outcome
from app.crawl.robots import Robots, RobotsFetch, RobotsStatus
from app.crawl.urls import TrackingParams, canonicalize, host_of, same_site
from app.db.web import CrawlCycle, Domain, FrontierEntry, RawPage, Url
from app.enums import DomainStatus, FrontierReason
from app.settings import Settings


@dataclass(frozen=True)
class PlannedFetch:
    url_id: int
    url: str
    host: str
    priority: float
    position: Position
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class DomainInfo:
    id: int
    host: str
    status: DomainStatus
    robots_txt: str | None
    robots_fetched_at: datetime | None
    crawl_delay_s: float | None


@dataclass(frozen=True)
class PollTarget:
    """A domain whose feeds and sitemaps are polled this cycle."""

    host: str
    feed_urls: list[str]
    sample_url: str
    """One of the domain's URLs, for the origin (scheme) of its robots.txt."""
    external_hops: int
    """The domain's closest entry; feed items and sitemap URLs on it start here."""


@dataclass(frozen=True)
class PollState:
    url_id: int
    etag: str | None
    last_modified: str | None


def robots_update(
    fetched: RobotsFetch, cached_text: str | None, origin: str, user_agent: str
) -> tuple[str | None, DomainStatus, bool]:
    """(robots_txt, status, refreshed) to store after fetching robots.txt.

    A failed refresh keeps a cached copy without marking it fresh, so it is retried next
    cycle; with no cached copy the domain is unreachable and nothing may be fetched.
    """
    if fetched.status is not RobotsStatus.UNREACHABLE:
        text, refreshed = fetched.text, True
    elif cached_text is not None:
        text, refreshed = cached_text, False
    else:
        return None, DomainStatus.UNREACHABLE, True
    allowed = Robots(text, user_agent).allows(f"{origin}/")
    return text, DomainStatus.ACTIVE if allowed else DomainStatus.BLOCKED, refreshed


def site_sitemaps(robots: Robots, host: str, tracking: TrackingParams) -> list[str]:
    """The sitemaps robots.txt lists for the site itself (a sitemap may only list URLs of its
    own site), canonicalized."""
    urls = (canonicalize(url, tracking) for url in robots.sitemaps)
    return sorted({url for url in urls if url and same_site(host_of(url), host)})


PLANNED = (
    sa.select(
        FrontierEntry.url_id,
        Url.url,
        Domain.host,
        FrontierEntry.priority,
        FrontierEntry.internal_depth,
        FrontierEntry.external_hops,
        Url.etag,
        Url.last_modified,
    )
    .join(Url, Url.id == FrontierEntry.url_id)
    .join(Domain, Domain.id == Url.domain_id)
)


class CrawlStore:
    def __init__(self, session: AsyncSession, cycle: CrawlCycle, settings: Settings) -> None:
        self._session = session
        self._lock = asyncio.Lock()
        self._settings = settings
        self._tracking = TrackingParams(settings.tracking_params)
        self.cycle = cycle
        stage = cycle.stats.get("fetch")
        self.stage: dict[str, object] = dict(stage) if isinstance(stage, dict) else {}
        counts = self.stage.get("counts")
        self.counts: Counter[str] = Counter(counts if isinstance(counts, dict) else {})

    async def _commit(self) -> None:
        self.cycle.stats = {
            **self.cycle.stats,
            "fetch": {**self.stage, "counts": dict(self.counts)},
        }
        await self._session.commit()

    async def set_stage(self, **values: object) -> None:
        async with self._lock:
            self.stage.update(values)
            await self._commit()

    # Domains and robots.txt

    async def domain(self, host: str) -> DomainInfo:
        async with self._lock:
            domain_id = (await frontier.ensure_domains(self._session, [host]))[host]
            domain = await self._session.get_one(Domain, domain_id, populate_existing=True)
            await self._commit()
            return DomainInfo(
                domain.id,
                domain.host,
                domain.status,
                domain.robots_txt,
                domain.robots_fetched_at,
                domain.crawl_delay_s,
            )

    async def save_robots(
        self, domain: DomainInfo, origin: str, fetched: RobotsFetch, now: datetime
    ) -> Robots:
        user_agent = self._settings.user_agent
        text, status, refreshed = robots_update(fetched, domain.robots_txt, origin, user_agent)
        robots = Robots(text, user_agent)
        async with self._lock:
            row = await self._session.get_one(Domain, domain.id, populate_existing=True)
            row.status = status
            row.robots_txt = text
            if refreshed:
                row.robots_fetched_at = now
                row.crawl_delay_s = robots.crawl_delay_s
                row.sitemap_urls = site_sitemaps(robots, domain.host, self._tracking)
            self.counts[f"robots.{fetched.status}"] += 1
            await self._commit()
        return robots

    # Feeds and sitemaps

    async def poll_targets(self) -> list[PollTarget]:
        """Domains in reach with feeds, or close enough to a seed for their sitemaps (which
        robots.txt lists) to be polled."""
        hops = sa.func.min(FrontierEntry.external_hops)
        async with self._lock:
            rows = await self._session.execute(
                sa.select(Domain.host, Domain.feed_urls, sa.func.min(Url.url), hops)
                .join(Url, Url.domain_id == Domain.id)
                .join(FrontierEntry, FrontierEntry.url_id == Url.id)
                .group_by(Domain.id)
                .having(
                    sa.or_(
                        sa.func.cardinality(Domain.feed_urls) > 0,
                        hops <= self._settings.sitemap_max_external_hops,
                    )
                )
                .order_by(Domain.host)
            )
            return [PollTarget(*row) for row in rows.tuples()]

    async def poll_state(self, url: str) -> PollState:
        async with self._lock:
            url_id = (await frontier.ensure_urls(self._session, [url]))[url]
            row = await self._session.get_one(Url, url_id, populate_existing=True)
            await self._commit()
            return PollState(row.id, row.etag, row.last_modified)

    async def record_poll(self, state: PollState, result: FetchResult, now: datetime) -> bool:
        """Record a feed or sitemap fetch; True if it returned new content to parse."""
        async with self._lock:
            url = await self._session.get_one(Url, state.url_id, populate_existing=True)
            changed = self._record_request(url, result, now)
            self.counts[f"poll.{result.outcome}"] += 1
            await self._commit()
            return changed

    async def replace_feed(self, host: str, old: str, new: str) -> None:
        """A feed moved permanently: poll its new address from now on."""
        async with self._lock:
            await self._session.execute(
                sa.text(
                    "UPDATE web.domains SET feed_urls = ARRAY(SELECT DISTINCT "
                    "unnest(array_replace(feed_urls, :old, :new)) ORDER BY 1) WHERE host = :host"
                ),
                {"host": host, "old": old, "new": new},
            )
            await self._commit()

    async def enqueue(self, candidates: list[Candidate]) -> int:
        async with self._lock:
            changes = await frontier.enqueue(self._session, candidates, self._settings)
            added = sum(changes.values())
            self.counts["enqueued"] += added
            await self._commit()
            return added

    # Planning

    async def release_stale_plans(self) -> None:
        async with self._lock:
            await frontier.release_stale_plans(self._session, self.cycle.id)
            await self._commit()

    async def plan(self, *, window_s: float, now: datetime, exclude_domain_ids: set[int]) -> int:
        """Plan more entries if the budget has room beyond what is already planned."""
        async with self._lock:
            unfetched = await self._session.scalar(
                sa.select(sa.func.count()).where(FrontierEntry.cycle_id == self.cycle.id)
            )
            room = self.cycle.page_budget - self.cycle.pages_fetched - (unfetched or 0)
            planned = 0
            if room > 0:
                planned = await frontier.plan(
                    self._session,
                    cycle_id=self.cycle.id,
                    room=room,
                    window_s=window_s,
                    settings=self._settings,
                    now=now,
                    exclude_domain_ids=exclude_domain_ids,
                )
            self.counts["planned"] += planned
            await self._commit()
            return planned

    async def planned(self, exclude_domain_ids: set[int]) -> list[PlannedFetch]:
        async with self._lock:
            rows = await self._session.execute(
                PLANNED.where(
                    FrontierEntry.cycle_id == self.cycle.id,
                    Url.domain_id.not_in(exclude_domain_ids),
                )
            )
            return [self._planned(*row) for row in rows.tuples()]

    @staticmethod
    def _planned(
        url_id: int,
        url: str,
        host: str,
        priority: float,
        depth: int,
        hops: int,
        etag: str | None,
        last_modified: str | None,
    ) -> PlannedFetch:
        return PlannedFetch(url_id, url, host, priority, Position(depth, hops), etag, last_modified)

    # Page fetches

    async def postpone(self, entry: PlannedFetch, until: datetime, reason: str) -> None:
        """Skip an entry without a request (robots.txt disallows it) until `until`."""
        async with self._lock:
            row = await self._session.get(FrontierEntry, entry.url_id, populate_existing=True)
            if row is not None:
                row.cycle_id = None
                row.next_fetch_at = until
            self.counts[f"skipped.{reason}"] += 1
            await self._commit()

    async def record_fetch(
        self, entry: PlannedFetch, result: FetchResult, now: datetime
    ) -> PlannedFetch | None:
        """Record a page fetch and reschedule or drop its entry. For a redirect, returns the
        target when it was added to this cycle's plan."""
        async with self._lock:
            url = await self._session.get_one(Url, entry.url_id, populate_existing=True)
            row = await self._session.get(FrontierEntry, entry.url_id, populate_existing=True)
            changed = self._record_request(url, result, now)
            if result.outcome is Outcome.OK and changed and result.body is not None:
                await self._store_body(entry.url_id, result, now)

            target = None
            keep = result.outcome in (Outcome.OK, Outcome.NOT_MODIFIED)
            if result.outcome is Outcome.REDIRECT:
                target = await self._redirect(url, entry, result, now)
                keep = target is not None and not result.permanent_redirect
            if row is not None:
                if keep:
                    self._reschedule(row, now)
                elif (
                    result.outcome is Outcome.RETRY
                    and row.failures + 1 < self._settings.fetch_max_failures
                ):
                    self._retry_later(row, now)
                else:
                    await self._session.delete(row)

            self.cycle.pages_fetched += 1
            self.counts[f"fetch.{result.outcome}"] += 1
            if result.detail:
                self.counts[f"detail.{result.detail}"] += 1
            await self._session.execute(
                sa.update(Domain).where(Domain.host == entry.host).values(last_crawled_at=now)
            )
            planned = await self._plan_target(target, now) if target is not None else None
            await self._commit()
            return planned

    def _record_request(self, url: Url, result: FetchResult, now: datetime) -> bool:
        """Update a URL's fetch bookkeeping; True if the response carried new content."""
        url.last_fetched_at = now
        url.fetch_count += 1
        url.http_status = result.status
        if result.outcome is Outcome.NOT_MODIFIED:
            url.etag = result.etag or url.etag
            url.last_modified = result.last_modified or url.last_modified
        if result.outcome is not Outcome.OK or result.body is None:
            return False
        digest = hashlib.sha256(result.body).hexdigest()
        changed = url.content_hash != digest
        if changed and url.content_hash is not None:
            url.change_count += 1
        url.content_hash = digest
        url.etag = result.etag
        url.last_modified = result.last_modified
        url.redirect_to_url_id = None
        return changed

    async def _store_body(self, url_id: int, result: FetchResult, now: datetime) -> None:
        values = {
            "url_id": url_id,
            "cycle_id": self.cycle.id,
            "fetched_at": now,
            "content_type": result.content_type or "",
            "charset": result.charset,
            "body": result.body,
        }
        insert = pg_insert(RawPage).values(values)
        await self._session.execute(
            insert.on_conflict_do_update(
                index_elements=[RawPage.url_id],
                set_={name: insert.excluded[name] for name in values if name != "url_id"},
            )
        )
        self.counts["stored"] += 1

    async def _redirect(
        self, url: Url, entry: PlannedFetch, result: FetchResult, now: datetime
    ) -> str | None:
        """Enqueue a redirect's target; None if it can't be crawled or points back here."""
        target = canonicalize(result.location or "", self._tracking)
        if target is None or target == entry.url:
            return None
        # A move within the site keeps the position; a move to another site is an external hop.
        internal = same_site(host_of(target), entry.host)
        position = entry.position if internal else entry.position.follow(internal=False)
        await frontier.enqueue(self._session, [Candidate(target, position)], self._settings)
        url.redirect_to_url_id = (await frontier.ensure_urls(self._session, [target]))[target]
        return target

    async def _plan_target(self, target: str, now: datetime) -> PlannedFetch | None:
        """Add a redirect target to this cycle's plan if it is due and not yet planned."""
        url_id = await self._session.scalar(
            sa.update(FrontierEntry)
            .where(
                FrontierEntry.url_id
                == sa.select(Url.id).where(Url.url == target).scalar_subquery(),
                FrontierEntry.cycle_id.is_(None),
                FrontierEntry.next_fetch_at <= now,
            )
            .values(cycle_id=self.cycle.id)
            .returning(FrontierEntry.url_id)
        )
        if url_id is None:
            return None
        rows = await self._session.execute(PLANNED.where(FrontierEntry.url_id == url_id))
        return self._planned(*rows.tuples().one())

    def _reschedule(self, row: FrontierEntry, now: datetime) -> None:
        row.reason = FrontierReason.RECRAWL
        row.cycle_id = None
        row.failures = 0
        row.next_fetch_at = now + timedelta(hours=self._settings.recrawl_after_h)

    def _retry_later(self, row: FrontierEntry, now: datetime) -> None:
        row.cycle_id = None
        row.failures += 1
        delay_h = self._settings.fetch_retry_base_h * 2 ** (row.failures - 1)
        row.next_fetch_at = now + timedelta(hours=delay_h)
