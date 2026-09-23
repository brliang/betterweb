"""Verify every suggested source in data/suggested_sources.toml.

    uv run python -m scripts.verify_sources [--max-age-days 180]

For each source, checks that robots.txt lets our user agent fetch the homepage and the feed,
that the feed parses with entries, and that its newest entry is recent. Exits 1 if any source
fails. It makes a few polite requests per site with the crawler's user agent; a site that
refuses us is dropped from the list, never worked around (PLAN.md principle 4).
"""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import feedparser  # type: ignore[import-untyped]  # ships no type information
import httpx2

from app.crawl.robots import MAX_ROBOTS_REDIRECTS, Robots, RobotsStatus, fetch_robots
from app.settings import get_settings
from app.suggested_sources import SuggestedSource, load_suggested_sources

logger = logging.getLogger("scripts.verify_sources")

CONCURRENCY = 8
TIMEOUT_S = 20
DEFAULT_MAX_AGE_DAYS = 180


@dataclass
class Report:
    source: SuggestedSource
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    entries: int = 0
    newest: datetime | None = None


class RobotsCache:
    """One robots.txt fetch per origin, shared by concurrent checks, with the rules applied
    exactly as the crawler applies them (app.crawl.robots)."""

    def __init__(self, client: httpx2.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._tasks: dict[str, asyncio.Task[Robots]] = {}

    def get(self, url: str) -> Awaitable[Robots]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._tasks:
            self._tasks[origin] = asyncio.create_task(self._fetch(origin))
        return self._tasks[origin]

    async def _fetch(self, origin: str) -> Robots:
        fetched = await fetch_robots(self._client, origin)
        unreachable = fetched.status is RobotsStatus.UNREACHABLE
        return Robots(None if unreachable else fetched.text, self._user_agent)


def newest_entry(entries: list[dict[str, object]]) -> datetime | None:
    dates = []
    for entry in entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if isinstance(stamp, time.struct_time):
            dates.append(datetime(*stamp[:6], tzinfo=UTC))
    return max(dates, default=None)


def parse_feed(content: bytes) -> list[dict[str, object]]:
    parsed = feedparser.parse(content)
    return [dict(entry) for entry in parsed.entries]


async def check(
    client: httpx2.AsyncClient,
    robots: RobotsCache,
    source: SuggestedSource,
    max_age: timedelta,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Report:
    report = Report(source)
    for url in (str(source.url), str(source.feed)):
        if not (await robots.get(url)).allows(url):
            report.problems.append(f"robots.txt disallows {url}")
    if report.problems:
        return report

    try:
        response = await client.get(str(source.feed), follow_redirects=True)
    except httpx2.TransportError as error:
        report.problems.append(f"feed unreachable: {error!r}")
        return report
    if response.status_code != 200:
        report.problems.append(f"feed returned {response.status_code}")
        return report
    if any(hop.status_code in (301, 308) for hop in response.history):
        report.notes.append(f"feed moved permanently to {response.url}")

    entries = parse_feed(response.content)
    report.entries = len(entries)
    report.newest = newest_entry(entries)
    if not entries:
        report.problems.append("feed has no entries")
    elif report.newest is None:
        report.notes.append("feed entries have no dates")
    elif report.newest < now() - max_age:
        report.problems.append(f"newest entry is from {report.newest:%Y-%m-%d}")
    elif report.newest > now():
        report.notes.append("some entries are dated in the future")
    return report


async def verify(max_age_days: int) -> int:
    settings = get_settings()
    sources = load_suggested_sources()
    limit = asyncio.Semaphore(CONCURRENCY)
    async with httpx2.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=TIMEOUT_S,
        max_redirects=MAX_ROBOTS_REDIRECTS,
    ) as client:
        robots = RobotsCache(client, settings.user_agent)

        async def one(source: SuggestedSource) -> Report:
            async with limit:
                return await check(client, robots, source, timedelta(days=max_age_days))

        reports = await asyncio.gather(*(one(source) for source in sources))

    for report in reports:
        newest = f"{report.newest:%Y-%m-%d}" if report.newest else "-"
        status = "FAIL" if report.problems else "ok"
        details = "; ".join([*report.problems, *report.notes])
        logger.info(
            "%-4s %-40s %3d entries, newest %s %s",
            status,
            report.source.name,
            report.entries,
            newest,
            details,
        )
    failed = sum(1 for report in reports if report.problems)
    logger.info("%d of %d sources verified", len(reports) - failed, len(reports))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.verify_sources")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="fail a feed whose newest entry is older than this",
    )
    args = parser.parse_args(argv)
    return asyncio.run(verify(args.max_age_days))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # one line per request otherwise
    sys.exit(main())
