"""The fetch stage of a crawl cycle (PLAN.md §6.1 step 1).

1. Poll the feeds and sitemaps of domains in reach, and enqueue their new items.
2. Plan: mark the best due frontier entries for this cycle, up to the page budget.
3. Fetch the planned entries, each domain at its own polite pace, highest priority first.
   Entries that turn out not to need a request (robots.txt disallows them) free up budget, so
   planning and fetching repeat until the budget, the time limit or the due entries run out.

Every request goes through its domain's robots.txt and DomainGate, polls included; hosts under
one registrable domain share a gate. A kill at any point is safe: a re-run skips polling if it
finished and resumes the planned entries.

A cycle runs up to CYCLE_FETCH_ROUNDS rounds of fetch then extract (see `start_next_round`);
only the first polls, and later ones fetch the links the extract stage just enqueued.
"""

import asyncio
import heapq
import itertools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.frontier import Candidate, Position
from app.crawl.http import ALLOWED_CONTENT_TYPES, FetchResult, Outcome, fetch
from app.crawl.politeness import DeadlineReached, DomainGate, PrioritySlots
from app.crawl.robots import Robots, fetch_robots
from app.crawl.sources import (
    SITEMAP_EXTRA_TYPES,
    SourceItem,
    newest,
    order_sitemaps,
    parse_feed,
    parse_sitemap,
)
from app.crawl.store import CrawlStore, DomainInfo, PlannedFetch, PollTarget, site_sitemaps
from app.crawl.urls import (
    TrackingParams,
    canonicalize,
    host_of,
    origin_of,
    registrable_domain,
    same_site,
)
from app.db.web import CrawlCycle
from app.settings import Settings

logger = logging.getLogger(__name__)

POLL_PRIORITY = 0.0
"""Polls run before any page fetch, so their priority only orders them among themselves."""
MAX_POLL_REDIRECTS = 5
"""Hops followed when a feed has moved (feeds move often; see the suggested-sources history)."""
SITEMAP_TYPES = ALLOWED_CONTENT_TYPES | SITEMAP_EXTRA_TYPES


class StopReason(StrEnum):
    DONE = "done"
    """No due entries left to fetch."""
    BUDGET = "budget"
    TIME = "time"


@dataclass
class _Domain:
    info: DomainInfo
    gate: DomainGate
    robots_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    robots: Robots | None = None
    queue: list[tuple[float, int, PlannedFetch]] = field(default_factory=list)
    workers: int = 0


class FetchStage:
    def __init__(
        self,
        store: CrawlStore,
        client: httpx2.AsyncClient,
        settings: Settings,
        *,
        deadline: datetime,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._client = client
        self._settings = settings
        self._now = now
        self._tracking = TrackingParams(settings.tracking_params)
        self._deadline = time.monotonic() + (deadline - now()).total_seconds()
        self._robots_ttl = timedelta(hours=settings.robots_ttl_h)
        self._slots = PrioritySlots(settings.global_concurrency)
        self._domains: dict[str, _Domain] = {}
        self._gates: dict[str, DomainGate] = {}
        """One per registrable domain: every `*.bearblog.dev` blog shares bearblog.dev's."""
        self._domains_lock = asyncio.Lock()
        self._order = itertools.count()
        self._tasks: asyncio.TaskGroup | None = None
        self._budget_left = 0
        self._stopped: StopReason | None = None

    async def run(self) -> StopReason:
        store = self._store
        if isinstance(done := store.stage.get("stopped"), str):
            return StopReason(done)
        if time.monotonic() >= self._deadline:
            self._stopped = StopReason.TIME
        if self._stopped is None and not store.stage.get("polled"):
            await self._poll_all()
            if self._stopped is None:
                await store.set_stage(polled=True)
        if self._stopped is None:
            await store.release_stale_plans()
            await self._fetch_rounds()
        reason = self._stopped or StopReason.DONE
        await store.set_stage(stopped=reason.value)
        logger.info(
            "fetch stage round %d stopped (%s): %d pages fetched; %s",
            store.round,
            reason,
            store.cycle.pages_fetched,
            dict(sorted(store.counts.items())),
        )
        return reason

    def _stop(self, reason: StopReason) -> None:
        self._stopped = self._stopped or reason

    def _exhausted(self) -> set[int]:
        """Domains that kept failing this cycle; left alone until the next one."""
        return {domain.info.id for domain in self._domains.values() if domain.gate.exhausted}

    # Shared plumbing

    async def _domain(self, host: str) -> _Domain:
        async with self._domains_lock:
            if host not in self._domains:
                info = await self._store.domain(host)
                settings = self._settings
                delay_s = max(settings.per_domain_min_delay_s, info.crawl_delay_s or 0)
                registered = registrable_domain(host)
                gate = self._gates.get(registered)
                if gate is None:
                    gate = self._gates[registered] = DomainGate(
                        delay_s=delay_s,
                        concurrency=settings.per_domain_concurrency,
                        backoff_max_s=settings.domain_backoff_max_s,
                        max_errors=settings.domain_max_consecutive_errors,
                    )
                else:
                    # A shared gate goes at the pace of its slowest host's Crawl-delay.
                    gate.delay_s = max(gate.delay_s, delay_s)
                self._domains[host] = _Domain(info, gate)
            return self._domains[host]

    async def _robots(self, domain: _Domain, origin: str, priority: float) -> Robots | None:
        """The domain's robots.txt rules, fetched at most once per cycle and per ROBOTS_TTL_H;
        None if the deadline arrived first."""
        async with domain.robots_lock:
            if domain.robots is None:
                info = domain.info
                fetched_at = info.robots_fetched_at
                if fetched_at is not None and self._now() - fetched_at < self._robots_ttl:
                    domain.robots = Robots(info.robots_txt, self._settings.user_agent)
                else:
                    try:
                        async with domain.gate.turn(self._slots, priority, self._deadline):
                            fetched = await fetch_robots(self._client, origin)
                    except DeadlineReached:
                        self._stop(StopReason.TIME)
                        return None
                    domain.robots = await self._store.save_robots(
                        info, origin, fetched, self._now()
                    )
                if (delay := domain.robots.crawl_delay_s) is not None:
                    domain.gate.delay_s = max(domain.gate.delay_s, delay)
            return domain.robots

    async def _request(
        self,
        domain: _Domain,
        url: str,
        priority: float,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        allowed_types: frozenset[str] = ALLOWED_CONTENT_TYPES,
        page: bool = False,
    ) -> FetchResult | None:
        """One request in the domain's turn; None if the budget (for pages) or time ran out."""
        try:
            async with domain.gate.turn(self._slots, priority, self._deadline):
                if page:
                    if self._budget_left <= 0:
                        self._stop(StopReason.BUDGET)
                        return None
                    self._budget_left -= 1
                result = await fetch(
                    self._client,
                    url,
                    max_bytes=self._settings.max_page_bytes,
                    now=self._now(),
                    etag=etag,
                    last_modified=last_modified,
                    allowed_types=allowed_types,
                )
                if result.outcome is Outcome.RETRY:
                    domain.gate.failed(result.retry_after_s)
                else:
                    domain.gate.succeeded()
                return result
        except DeadlineReached:
            self._stop(StopReason.TIME)
            return None

    # Step 1: feeds and sitemaps

    async def _poll_all(self) -> None:
        targets = await self._store.poll_targets()
        async with asyncio.TaskGroup() as tasks:
            for target in targets:
                tasks.create_task(self._poll_target(target))

    async def _poll_target(self, target: PollTarget) -> None:
        for feed in target.feed_urls:
            if self._stopped is None:
                await self._poll_feed(target, feed)
        if target.external_hops > self._settings.sitemap_max_external_hops:
            return
        domain = await self._domain(target.host)
        robots = await self._robots(domain, origin_of(target.sample_url), POLL_PRIORITY)
        if self._stopped is None and robots is not None:
            sitemaps = order_sitemaps(
                site_sitemaps(robots, target.host, self._tracking),
                self._settings.sitemap_skip_words,
            )
            if sitemaps:
                await self._poll_sitemaps(target, sitemaps)

    async def _poll(
        self, url: str, *, conditional: bool, allowed_types: frozenset[str]
    ) -> tuple[FetchResult, bool] | None:
        """Fetch a feed or sitemap: (result, whether its content changed), or None if the
        request wasn't made (robots.txt, time)."""
        domain = await self._domain(host_of(url))
        robots = await self._robots(domain, origin_of(url), POLL_PRIORITY)
        if robots is None:
            return None
        if not robots.allows(url):
            self._store.counts["skipped.robots"] += 1
            return None
        state = await self._store.poll_state(url)
        result = await self._request(
            domain,
            url,
            POLL_PRIORITY,
            etag=state.etag if conditional else None,
            last_modified=state.last_modified if conditional else None,
            allowed_types=allowed_types,
        )
        if result is None:
            return None
        return result, await self._store.record_poll(state, result, self._now())

    def _candidates(
        self, items: list[SourceItem], target: PollTarget, *, same_site_only: bool
    ) -> list[Candidate]:
        """Feed items and sitemap URLs start at depth 0; one on another site is a hop away."""
        candidates = []
        hops = target.external_hops
        for item in items:
            url = canonicalize(item.url, self._tracking)
            if url is None:
                continue
            if same_site(host_of(url), target.host):
                candidates.append(Candidate(url, Position(0, hops)))
            elif not same_site_only:
                candidates.append(Candidate(url, Position(0, hops + 1)))
        return candidates

    async def _poll_feed(self, target: PollTarget, feed_url: str) -> None:
        url = feed_url
        for _ in range(MAX_POLL_REDIRECTS + 1):
            polled = await self._poll(url, conditional=True, allowed_types=ALLOWED_CONTENT_TYPES)
            if polled is None:
                return
            result, changed = polled
            if result.outcome is Outcome.REDIRECT:
                moved = canonicalize(result.location or "", self._tracking)
                if moved is None or moved == url:
                    return
                if result.permanent_redirect:
                    await self._store.replace_feed(target.host, url, moved)
                url = moved
                continue
            if changed and result.body is not None:
                items = parse_feed(result.body, url)
                self._store.counts["feed_items"] += len(items)
                chosen = newest(items, self._settings.feed_max_items, now=self._now())
                await self._store.enqueue(self._candidates(chosen, target, same_site_only=False))
            return

    async def _poll_sitemaps(self, target: PollTarget, roots: list[str]) -> None:
        settings = self._settings
        queue = list(roots)
        seen: set[str] = set()
        items: list[SourceItem] = []
        while queue and len(seen) < settings.sitemap_max_files_per_domain:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            polled = await self._poll(url, conditional=False, allowed_types=SITEMAP_TYPES)
            if polled is None:
                if self._stopped is not None:
                    return
                continue
            result, _ = polled
            if result.outcome is not Outcome.OK or result.body is None:
                continue
            sitemap = parse_sitemap(result.body, settings.max_page_bytes)
            if sitemap is None:
                self._store.counts["sitemap_invalid"] += 1
            elif sitemap.is_index:
                # Newest child sitemaps first; they list the newest pages.
                children = newest(sitemap.items, len(sitemap.items), now=self._now())
                for child in order_sitemaps(
                    (
                        canonical
                        for c in children
                        if (canonical := canonicalize(c.url, self._tracking))
                    ),
                    settings.sitemap_skip_words,
                ):
                    if same_site(host_of(child), target.host):
                        queue.append(child)
            else:
                items.extend(sitemap.items)
        self._store.counts["sitemap_items"] += len(items)
        chosen = newest(
            items,
            settings.sitemap_max_urls_per_domain,
            now=self._now(),
            max_age=timedelta(days=settings.sitemap_max_age_days),
        )
        await self._store.enqueue(self._candidates(chosen, target, same_site_only=True))

    # Steps 2 and 3: plan and fetch

    async def _fetch_rounds(self) -> None:
        store = self._store
        while self._stopped is None:
            window_s = self._deadline - time.monotonic()
            if window_s <= 0:
                self._stop(StopReason.TIME)
                break
            self._budget_left = store.cycle.page_budget - store.cycle.pages_fetched
            if self._budget_left <= 0:
                self._stop(StopReason.BUDGET)
                break
            await store.plan(
                window_s=window_s, now=self._now(), exclude_domain_ids=self._exhausted()
            )
            entries = await store.planned(self._exhausted())
            if not entries:
                break
            logger.info("fetching %d planned pages", len(entries))
            async with asyncio.TaskGroup() as tasks:
                self._tasks = tasks
                for entry in entries:
                    await self._schedule(entry)
            self._tasks = None
        store.counts["exhausted_domains"] = len(self._exhausted())

    async def _schedule(self, entry: PlannedFetch) -> None:
        domain = await self._domain(entry.host)
        heapq.heappush(domain.queue, (-entry.priority, next(self._order), entry))
        if self._tasks is not None and domain.workers < self._settings.per_domain_concurrency:
            domain.workers += 1
            self._tasks.create_task(self._drain(domain))

    async def _drain(self, domain: _Domain) -> None:
        try:
            while domain.queue and self._stopped is None and not domain.gate.exhausted:
                _, _, entry = heapq.heappop(domain.queue)
                await self._fetch_page(domain, entry)
        finally:
            domain.workers -= 1

    async def _fetch_page(self, domain: _Domain, entry: PlannedFetch) -> None:
        robots = await self._robots(domain, origin_of(entry.url), entry.priority)
        if robots is None:
            return
        if not robots.allows(entry.url):
            # Re-checked when robots.txt is next refreshed; costs no budget.
            await self._store.postpone(entry, self._now() + self._robots_ttl, "robots")
            return
        result = await self._request(
            domain,
            entry.url,
            entry.priority,
            etag=entry.etag,
            last_modified=entry.last_modified,
            page=True,
        )
        if result is None:
            return
        target = await self._store.record_fetch(entry, result, self._now())
        if target is not None:
            await self._schedule(target)


async def start_next_round(session: AsyncSession, cycle: CrawlCycle, settings: Settings) -> bool:
    """Let the fetch stage run again, to fetch what the extract stage enqueued after the last
    round. False when CYCLE_FETCH_ROUNDS were run or the last round stopped early (a budget or
    time stop ends the cycle's fetching)."""
    store = CrawlStore(session, cycle, settings)
    if store.stage.get("stopped") != StopReason.DONE or store.round >= settings.cycle_fetch_rounds:
        return False
    del store.stage["stopped"]
    await store.set_stage(round=store.round + 1)
    return True


async def run_fetch_stage(
    session: AsyncSession, cycle: CrawlCycle, client: httpx2.AsyncClient, settings: Settings
) -> StopReason:
    """Run (or resume) the fetch stage of `cycle`. Its time limit counts from the cycle's start."""
    store = CrawlStore(session, cycle, settings)
    deadline = cycle.started_at + timedelta(hours=settings.cycle_time_limit_h)
    return await FetchStage(store, client, settings, deadline=deadline).run()
