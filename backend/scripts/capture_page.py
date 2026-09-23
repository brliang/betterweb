"""Save a real page as an extraction test fixture (PLAN.md §11 M4).

    uv run python -m scripts.capture_page URL NAME

Checks robots.txt for our user agent first, then fetches the page once with the crawler's own
request code (content-type allowlist, size limit). Writes tests/fixtures/pages/NAME.gz and
appends an entry to tests/fixtures/pages/manifest.toml; fill in its `license` and expected
values by hand. Only save pages whose license allows redistribution.
"""

import argparse
import asyncio
import gzip
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.crawl.http import Outcome, create_client, fetch
from app.crawl.robots import Robots, RobotsStatus, fetch_robots
from app.crawl.urls import TrackingParams, canonicalize, origin_of
from app.settings import get_settings

logger = logging.getLogger("scripts.capture_page")

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pages"


def manifest_entry(
    name: str,
    url: str,
    content_type: str,
    charset: str | None,
    robots_tag: str | None,
    fetched_at: datetime,
) -> str:
    lines = [
        "",
        "[[page]]",
        f'name = "{name}"',
        f'url = "{url}"',
        f'content_type = "{content_type}"',
    ]
    if charset:
        lines.append(f'charset = "{charset}"')
    if robots_tag:
        lines.append(f"robots_tag = {robots_tag!r}")
    lines += [
        f'fetched_at = "{fetched_at.date().isoformat()}"',
        'license = "TODO"',
        "",
        "[page.expected]",
        'type = "TODO"',
        "",
    ]
    return "\n".join(lines)


async def capture(url: str, name: str) -> int:
    settings = get_settings()
    canonical = canonicalize(url, TrackingParams(settings.tracking_params))
    if canonical is None:
        logger.error("not a crawlable URL: %s", url)
        return 1
    target = FIXTURES / f"{name}.gz"
    if target.exists():
        logger.error("%s already exists", target)
        return 1
    async with create_client(settings) as client:
        fetched = await fetch_robots(client, origin_of(canonical))
        text = None if fetched.status is RobotsStatus.UNREACHABLE else fetched.text
        if not Robots(text, settings.user_agent).allows(canonical):
            logger.error("robots.txt doesn't allow %s; pick another page", canonical)
            return 1
        now = datetime.now(UTC)
        result = await fetch(client, canonical, max_bytes=settings.max_page_bytes, now=now)
    if result.outcome is not Outcome.OK or result.body is None or result.content_type is None:
        logger.error("fetch failed: %s %s %s", result.outcome, result.status, result.detail or "")
        return 1
    target.write_bytes(gzip.compress(result.body, mtime=0))
    entry = manifest_entry(
        name, canonical, result.content_type, result.charset, result.robots_tag, now
    )
    with (FIXTURES / "manifest.toml").open("a") as manifest:
        manifest.write(entry)
    logger.info("saved %s (%d bytes); fill in its manifest entry", target, len(result.body))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.capture_page")
    parser.add_argument("url")
    parser.add_argument("name", help="fixture name: lowercase letters, digits and underscores")
    args = parser.parse_args(argv)
    return asyncio.run(capture(args.url, args.name))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
