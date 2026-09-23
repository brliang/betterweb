import httpx2
import pytest

from app.crawl.robots import MAX_ROBOTS_BYTES, Robots, RobotsFetch, RobotsStatus, fetch_robots
from app.crawl.store import robots_update
from app.enums import DomainStatus
from tests.fake_web import FakeWeb, redirect

pytestmark = pytest.mark.anyio

AGENT = "bribot/0.1 (+https://bot.example/about)"
ROBOTS_URL = "https://example.com/robots.txt"


@pytest.mark.parametrize(
    ("rules", "path", "allowed"),
    [
        ("", "/anything", True),
        ("User-agent: *\nDisallow: /", "/a", False),
        ("User-agent: *\nDisallow: /private", "/private/x", False),
        ("User-agent: *\nDisallow: /private", "/public", True),
        # The longest match wins, whatever the order (the stdlib parser takes the first).
        ("User-agent: *\nAllow: /\nDisallow: /private", "/private/x", False),
        ("User-agent: *\nDisallow: /\nAllow: /blog/", "/blog/post", True),
        # Wildcards and end anchors.
        ("User-agent: *\nDisallow: /*?", "/search?q=1", False),
        ("User-agent: *\nDisallow: /*.pdf$", "/paper.pdf", False),
        ("User-agent: *\nDisallow: /*.pdf$", "/paper.pdf.html", True),
        # A group for our product token beats the * group.
        ("User-agent: *\nDisallow: /\n\nUser-agent: bribot\nAllow: /", "/a", True),
        ("User-agent: *\nAllow: /\n\nUser-agent: BRIBOT\nDisallow: /", "/a", False),
        ("User-agent: OtherBot\nDisallow: /", "/a", True),
    ],
)
def test_rules(rules: str, path: str, allowed: bool) -> None:
    assert Robots(rules, AGENT).allows(f"https://example.com{path}") is allowed


def test_no_rules_means_nothing_is_allowed() -> None:
    robots = Robots(None, AGENT)
    assert not robots.allows("https://example.com/")
    assert robots.crawl_delay_s is None
    assert robots.sitemaps == []


def test_crawl_delay_and_sitemaps() -> None:
    robots = Robots(
        "User-agent: *\nCrawl-delay: 7\nSitemap: https://example.com/sitemap.xml\n", AGENT
    )
    assert robots.crawl_delay_s == 7
    assert robots.sitemaps == ["https://example.com/sitemap.xml"]


async def fetched(web: FakeWeb) -> RobotsFetch:
    async with httpx2.AsyncClient(transport=web.transport(), max_redirects=5) as client:
        return await fetch_robots(client, "https://example.com")


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (httpx2.Response(200, text="User-agent: *\nDisallow: /x"), RobotsStatus.FOUND),
        (httpx2.Response(404), RobotsStatus.MISSING),
        (httpx2.Response(403), RobotsStatus.MISSING),  # RFC 9309: any 4xx means no rules
        (httpx2.Response(500), RobotsStatus.UNREACHABLE),
        (httpx2.Response(503), RobotsStatus.UNREACHABLE),
    ],
)
async def test_fetch_statuses(response: httpx2.Response, status: RobotsStatus) -> None:
    assert (await fetched(FakeWeb({ROBOTS_URL: response}))).status is status


async def test_network_error_means_unreachable() -> None:
    async def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    assert (await fetched(FakeWeb({ROBOTS_URL: fail}))).status is RobotsStatus.UNREACHABLE


async def test_redirects_are_followed() -> None:
    web = FakeWeb(
        {
            ROBOTS_URL: redirect("https://www.example.com/robots.txt"),
            "https://www.example.com/robots.txt": httpx2.Response(200, text="User-agent: *"),
        }
    )
    assert await fetched(web) == RobotsFetch(RobotsStatus.FOUND, "User-agent: *")


async def test_too_many_redirects_means_no_rules() -> None:
    web = FakeWeb({ROBOTS_URL: redirect(ROBOTS_URL)})
    assert (await fetched(web)).status is RobotsStatus.MISSING


async def test_only_the_first_500_kib_is_parsed() -> None:
    web = FakeWeb({ROBOTS_URL: httpx2.Response(200, content=b"#" * (MAX_ROBOTS_BYTES + 10))})
    assert len((await fetched(web)).text) == MAX_ROBOTS_BYTES


ORIGIN = "https://example.com"
BLOCKING = "User-agent: *\nDisallow: /"


@pytest.mark.parametrize(
    ("fetch", "expected"),
    [
        (RobotsFetch(RobotsStatus.FOUND, "User-agent: *"), ("User-agent: *", "active")),
        (RobotsFetch(RobotsStatus.MISSING), ("", "active")),
        (RobotsFetch(RobotsStatus.FOUND, BLOCKING), (BLOCKING, "blocked")),
        (RobotsFetch(RobotsStatus.UNREACHABLE), (None, "unreachable")),
    ],
)
def test_robots_update(fetch: RobotsFetch, expected: tuple[str | None, str]) -> None:
    text, status = expected
    assert robots_update(fetch, None, ORIGIN, AGENT) == (text, DomainStatus(status), True)


@pytest.mark.parametrize(
    ("cached", "status"), [("User-agent: *\nDisallow: /private", "active"), (BLOCKING, "blocked")]
)
def test_a_failed_refresh_keeps_the_cached_copy_but_retries(cached: str, status: str) -> None:
    fetch = RobotsFetch(RobotsStatus.UNREACHABLE)
    assert robots_update(fetch, cached, ORIGIN, AGENT) == (cached, DomainStatus(status), False)
