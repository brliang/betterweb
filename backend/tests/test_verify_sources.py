from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from app.suggested_sources import SuggestedSource
from scripts.verify_sources import RobotsCache, check

pytestmark = pytest.mark.anyio

USER_AGENT = "bribot/0.1 (+https://example.invalid/bot)"
NOW = datetime(2026, 9, 23, tzinfo=UTC)
SOURCE = SuggestedSource.model_validate(
    {
        "name": "Example",
        "url": "https://example.com/",
        "feed": "https://example.com/feed.xml",
        "topics": ["596"],
        "description": "An example.",
    }
)


def feed(published: str) -> bytes:
    return f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example</title>
<item><title>Post</title><link>https://example.com/post</link><pubDate>{published}</pubDate></item>
</channel></rss>""".encode()


async def run(routes: dict[str, httpx2.Response]) -> list[str]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return routes.get(str(request.url), httpx2.Response(404))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        robots = RobotsCache(client, USER_AGENT)
        report = await check(client, robots, SOURCE, timedelta(days=180), lambda: NOW)
    return report.problems


async def test_healthy_source_passes() -> None:
    feed_ok = httpx2.Response(200, content=feed("Mon, 21 Sep 2026 10:00:00 GMT"))
    assert await run({"https://example.com/feed.xml": feed_ok}) == []


@pytest.mark.parametrize(
    ("robots", "problem"),
    [
        ("User-agent: bribot\nDisallow: /\n", "robots.txt disallows"),
        (
            "User-agent: *\nDisallow: /feed.xml\n",
            "robots.txt disallows https://example.com/feed.xml",
        ),
    ],
)
async def test_robots_disallow_fails(robots: str, problem: str) -> None:
    routes = {
        "https://example.com/robots.txt": httpx2.Response(200, text=robots),
        "https://example.com/feed.xml": httpx2.Response(
            200, content=feed("Mon, 21 Sep 2026 10:00:00 GMT")
        ),
    }
    [found, *_] = await run(routes)
    assert found.startswith(problem)


async def test_robots_server_error_means_disallowed() -> None:
    problems = await run({"https://example.com/robots.txt": httpx2.Response(503)})
    assert problems[0].startswith("robots.txt disallows")


async def test_stale_feed_fails() -> None:
    old = httpx2.Response(200, content=feed("Mon, 02 Jan 2023 10:00:00 GMT"))
    assert await run({"https://example.com/feed.xml": old}) == ["newest entry is from 2023-01-02"]


async def test_missing_feed_fails() -> None:
    assert await run({}) == ["feed returned 404"]
