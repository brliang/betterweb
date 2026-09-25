"""The fetch stage end to end, against an in-memory web (PLAN.md §6.1 step 1, milestone M3)."""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.cycle import finish_cycle, start_or_resume_cycle
from app.crawl.fetch_stage import FetchStage, StopReason, start_next_round
from app.crawl.frontier import Position, add_seed
from app.crawl.http import create_client
from app.crawl.store import CrawlStore
from app.db.web import CrawlCycle, Domain, FrontierEntry, RawPage, Url
from app.enums import DomainStatus, FrontierReason
from app.settings import Settings
from tests.fake_web import FakeWeb, Route, html, redirect, xml

pytestmark = pytest.mark.anyio

SETTINGS_OPTIONS: dict[str, object] = {
    "_env_file": None,
    "user_agent": "bribot/0.1 (+https://bot.example/about)",
    "per_domain_min_delay_s": 0,
    "per_domain_start_delay_s": 0,
    "domain_backoff_max_s": 0,
}
HOME = "https://example.com/"
FEED = "https://example.com/feed.xml"


# Frontier entries default to the database's now(), so the stage's clock must not be behind it.
NOW = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=1)


def settings(**overrides: object) -> Settings:
    return Settings(**{**SETTINGS_OPTIONS, **overrides})  # type: ignore[arg-type]


def rss(*links: str) -> httpx2.Response:
    items = "".join(f"<item><title>{link}</title><link>{link}</link></item>" for link in links)
    return xml(f'<rss version="2.0"><channel><title>t</title>{items}</channel></rss>')


def urlset(*entries: tuple[str, str]) -> httpx2.Response:
    body = "".join(f"<url><loc>{loc}</loc><lastmod>{mod}</lastmod></url>" for loc, mod in entries)
    return xml(f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>')


def robots(text: str) -> httpx2.Response:
    return httpx2.Response(200, text=text)


async def run(
    session: AsyncSession,
    web: FakeWeb,
    options: Settings | None = None,
    *,
    now: datetime | None = None,
) -> tuple[StopReason, CrawlCycle]:
    """Run the fetch stage of the running cycle (or a new one) at `now`."""
    options = options or settings()
    cycle = await start_or_resume_cycle(session, options)
    moment = now or datetime.now(UTC)
    store = CrawlStore(session, cycle, options)
    async with create_client(options, transport=web.transport()) as client:
        stage = FetchStage(
            store,
            client,
            options,
            deadline=moment + timedelta(hours=options.cycle_time_limit_h),
            now=lambda: moment,
        )
        return await stage.run(), cycle


async def url_row(session: AsyncSession, url: str) -> Url:
    row = await session.scalar(
        sa.select(Url).where(Url.url == url).execution_options(populate_existing=True)
    )
    assert row is not None, url
    return row


async def frontier(session: AsyncSession, url: str) -> FrontierEntry | None:
    row = await url_row(session, url)
    return await session.get(FrontierEntry, row.id, populate_existing=True)


def site() -> dict[str, Route]:
    """A small site: robots.txt with a disallowed area and a sitemap index, a homepage, a feed
    with a same-site, a disallowed and an external item, and a sitemap."""
    return {
        "https://example.com/robots.txt": robots(
            "User-agent: *\nDisallow: /private\n"
            "Sitemap: https://example.com/sitemap-index.xml\n"
            "Sitemap: https://cdn.example.net/sitemap.xml\n"
        ),
        HOME: html(),
        FEED: rss(
            "https://example.com/post-1",
            "https://example.com/private/secret",
            "https://other.example/linked",
        ),
        "https://example.com/sitemap-index.xml": xml(
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>"
            "</sitemapindex>"
        ),
        "https://example.com/sitemap-posts.xml": urlset(
            ("https://example.com/post-1", "2026-09-20"),
            ("https://example.com/from-sitemap", "2026-09-21"),
            ("https://example.com/ancient", "2020-01-01"),
            ("https://elsewhere.example/not-ours", "2026-09-21"),
        ),
        "https://example.com/post-1": html("<p>one</p>"),
        "https://example.com/from-sitemap": html("<p>s</p>"),
        "https://other.example/linked": html("<p>external</p>"),
    }


async def test_first_cycle_polls_plans_and_fetches(session: AsyncSession) -> None:
    web = FakeWeb(site())
    await add_seed(session, HOME, settings(), [FEED])
    reason, cycle = await run(session, web, now=NOW)

    assert reason is StopReason.DONE
    fetched_pages = {
        HOME,
        "https://example.com/post-1",
        "https://example.com/from-sitemap",
        "https://other.example/linked",
    }
    assert {url for url in web.urls if "robots" not in url and "xml" not in url} == fetched_pages
    # Each page, and each host's robots.txt, is fetched once; disallowed pages never.
    assert all(web.count(url) == 1 for url in fetched_pages)
    assert web.count("https://example.com/robots.txt") == 1
    assert "https://example.com/private/secret" not in web.urls
    assert not any("ancient" in url or "elsewhere" in url for url in web.urls)
    # Every request identifies the crawler.
    assert {r.headers["user-agent"] for r in web.requests} == {settings().user_agent}

    assert cycle.pages_fetched == 4  # robots.txt, feeds and sitemaps don't count
    counts = cycle.stats["fetch"]["counts"]  # type: ignore[index]
    assert counts["fetch.ok"] == 4
    assert counts["skipped.robots"] == 1
    assert cycle.stats["fetch"]["stopped"] == "done"  # type: ignore[index]

    # Fetched entries become re-crawls; their bodies wait for the extract stage.
    home = await frontier(session, HOME)
    assert home is not None
    assert (home.reason, home.cycle_id) == (FrontierReason.RECRAWL, None)
    assert home.next_fetch_at == NOW + timedelta(hours=settings().recrawl_after_h)
    bodies = await session.scalars(sa.select(RawPage.body).order_by(RawPage.body))
    assert sorted(bodies) == sorted(
        [b"<html><body>page</body></html>", b"<p>one</p>", b"<p>s</p>", b"<p>external</p>"]
    )
    # The disallowed item waits for the next robots.txt refresh, at no cost to the budget.
    secret = await frontier(session, "https://example.com/private/secret")
    assert secret is not None
    assert secret.next_fetch_at == NOW + timedelta(hours=settings().robots_ttl_h)
    # Feed items start at depth 0; an external one is a hop away.
    linked = await frontier(session, "https://other.example/linked")
    assert linked is not None
    assert Position(linked.internal_depth, linked.external_hops) == Position(0, 1)
    domain = await session.scalar(sa.select(Domain).where(Domain.host == "example.com"))
    assert domain is not None
    assert domain.sitemap_urls == ["https://example.com/sitemap-index.xml"]


async def test_the_next_cycle_uses_conditional_gets(session: AsyncSession) -> None:
    web = FakeWeb(
        {
            "https://example.com/robots.txt": robots("User-agent: *\nAllow: /"),
            HOME: [html("v1", etag='"home-1"'), httpx2.Response(304)],
            FEED: [
                httpx2.Response(
                    200,
                    text=rss("https://example.com/post").text,
                    headers={"content-type": "application/rss+xml", "etag": '"feed-1"'},
                ),
                httpx2.Response(304),
            ],
            "https://example.com/post": [
                html("first"),
                html("second", **{"x-robots-tag": "noindex"}),
            ],
        }
    )
    await add_seed(session, HOME, settings(), [FEED])
    _, first = await run(session, web, now=NOW)
    await finish_cycle(session, first)
    # Re-crawls are due after RECRAWL_AFTER_H (20); robots.txt stays fresh for 24.
    later = NOW + timedelta(hours=21)
    _, second = await run(session, web, now=later)

    assert second.id != first.id
    home_requests = [r for r in web.requests if str(r.url) == HOME]
    assert home_requests[1].headers["if-none-match"] == '"home-1"'
    feed_requests = [r for r in web.requests if str(r.url) == FEED]
    assert feed_requests[1].headers["if-none-match"] == '"feed-1"'
    # robots.txt is still fresh: not fetched again.
    assert web.count("https://example.com/robots.txt") == 1

    home = await url_row(session, HOME)
    post = await url_row(session, "https://example.com/post")
    assert (home.fetch_count, home.change_count, home.http_status) == (2, 0, 304)
    assert (post.fetch_count, post.change_count) == (2, 1)
    raw = await session.get(RawPage, post.id, populate_existing=True)
    assert raw is not None
    assert (raw.body, raw.cycle_id, raw.robots_tag) == (b"second", second.id, "noindex")


async def test_redirects_go_through_the_frontier(session: AsyncSession) -> None:
    web = FakeWeb(
        {
            HOME: redirect("https://www.example.com/"),
            "https://www.example.com/": html(),
            "https://www.example.com/robots.txt": robots(""),
            "https://example.com/temporary": redirect("/landing", 302),
            "https://example.com/landing": html(),
            "https://example.com/moved-away": redirect("https://elsewhere.example/p", 308),
            "https://elsewhere.example/p": html(),
        }
    )
    await add_seed(session, HOME, settings())
    await add_seed(session, "https://example.com/temporary", settings())
    await add_seed(session, "https://example.com/moved-away", settings())
    reason, cycle = await run(session, web, now=NOW)

    assert reason is StopReason.DONE
    # Targets were fetched in the same cycle, each once.
    for target in [
        "https://www.example.com/",
        "https://example.com/landing",
        "https://elsewhere.example/p",
    ]:
        assert web.count(target) == 1
    assert cycle.pages_fetched == 6

    home = await url_row(session, HOME)
    www = await url_row(session, "https://www.example.com/")
    assert (home.http_status, home.redirect_to_url_id) == (301, www.id)
    # A permanent redirect drops the old URL; a temporary one keeps it for re-crawls.
    assert await frontier(session, HOME) is None
    assert await frontier(session, "https://example.com/temporary") is not None
    assert await frontier(session, "https://example.com/moved-away") is None
    # Same site keeps the position; another site is a hop further out.
    www_entry = await frontier(session, "https://www.example.com/")
    elsewhere = await frontier(session, "https://elsewhere.example/p")
    assert www_entry is not None
    assert elsewhere is not None
    assert (www_entry.internal_depth, www_entry.external_hops) == (0, 0)
    assert (elsewhere.internal_depth, elsewhere.external_hops) == (0, 1)


async def test_the_budget_stops_the_stage(session: AsyncSession) -> None:
    pages = [f"https://example.com/{index}" for index in range(5)]
    web = FakeWeb({page: html() for page in pages})
    for page in pages:
        await add_seed(session, page, settings())
    reason, cycle = await run(session, web, settings(cycle_page_budget=3), now=NOW)

    assert reason is StopReason.BUDGET
    assert cycle.pages_fetched == 3
    assert len([url for url in web.urls if "robots" not in url]) == 3
    unfetched = await session.scalar(
        sa.select(sa.func.count()).where(
            FrontierEntry.cycle_id.is_(None), FrontierEntry.reason == FrontierReason.NEW
        )
    )
    assert unfetched == 2  # left for the next cycle


async def test_the_time_limit_stops_the_stage(session: AsyncSession) -> None:
    web = FakeWeb({HOME: html()})
    await add_seed(session, HOME, settings())
    options = settings()
    cycle = await start_or_resume_cycle(session, options)
    async with create_client(options, transport=web.transport()) as client:
        stage = FetchStage(
            CrawlStore(session, cycle, options), client, options, deadline=NOW, now=lambda: NOW
        )
        assert await stage.run() is StopReason.TIME
    assert web.requests == []
    # A finished stage isn't re-run when the cycle resumes.
    reason, _ = await run(session, web, now=NOW)
    assert reason is StopReason.TIME
    assert web.requests == []


async def test_failures_back_off_and_drop(session: AsyncSession) -> None:
    web = FakeWeb(
        {
            "https://example.com/missing": httpx2.Response(404),
            "https://example.com/busy": httpx2.Response(503, headers={"retry-after": "1"}),
            "https://example.com/image": httpx2.Response(
                200, headers={"content-type": "image/png"}
            ),
        }
    )
    for page in ["missing", "busy", "image"]:
        await add_seed(session, f"https://example.com/{page}", settings())
    await run(session, web, now=NOW)

    assert await frontier(session, "https://example.com/missing") is None
    assert await frontier(session, "https://example.com/image") is None
    busy = await frontier(session, "https://example.com/busy")
    assert busy is not None
    assert busy.failures == 1
    assert busy.next_fetch_at == NOW + timedelta(hours=settings().fetch_retry_base_h)
    assert (await url_row(session, "https://example.com/missing")).http_status == 404


async def test_a_url_that_keeps_failing_is_dropped(session: AsyncSession) -> None:
    web = FakeWeb({HOME: httpx2.Response(500)})
    options = settings(fetch_max_failures=2)
    await add_seed(session, HOME, options)
    moment = NOW
    for _ in range(2):
        _, cycle = await run(session, web, options, now=moment)
        await finish_cycle(session, cycle)
        moment += timedelta(days=3)
    assert web.count(HOME) == 2
    assert await frontier(session, HOME) is None


async def test_a_domain_that_keeps_failing_is_left_alone(session: AsyncSession) -> None:
    pages = [f"https://flaky.example/{index}" for index in range(5)]
    web = FakeWeb({page: httpx2.Response(503) for page in pages})
    web.routes["https://fine.example/"] = html()
    for page in [*pages, "https://fine.example/"]:
        await add_seed(session, page, settings())
    reason, cycle = await run(session, web, settings(domain_max_consecutive_errors=2), now=NOW)

    assert reason is StopReason.DONE
    assert len([url for url in web.urls if "flaky" in url and "robots" not in url]) == 2
    assert web.count("https://fine.example/") == 1
    assert cycle.stats["fetch"]["counts"]["exhausted_domains"] == 1  # type: ignore[index]


async def test_robots_txt_decides_what_is_fetched(session: AsyncSession) -> None:
    web = FakeWeb(
        {
            "https://closed.example/robots.txt": robots("User-agent: *\nDisallow: /"),
            "https://closed.example/": html(),
            "https://down.example/robots.txt": httpx2.Response(503),
            "https://down.example/": html(),
            "https://open.example/robots.txt": httpx2.Response(404),
            "https://open.example/": html(),
        }
    )
    for host in ["closed", "down", "open"]:
        await add_seed(session, f"https://{host}.example/", settings())
    _, cycle = await run(session, web, now=NOW)

    assert {url for url in web.urls if "robots" not in url} == {"https://open.example/"}
    assert cycle.pages_fetched == 1
    statuses = dict((await session.execute(sa.select(Domain.host, Domain.status))).tuples().all())
    assert statuses == {
        "closed.example": DomainStatus.BLOCKED,
        "down.example": DomainStatus.UNREACHABLE,
        "open.example": DomainStatus.ACTIVE,
    }


async def test_sitemaps_are_read_news_first_skipping_hub_listings(session: AsyncSession) -> None:
    """The file budget goes to the news sitemap, not to earlier-listed author and tag
    sitemaps, and its article beats a page whose lastmod is newer."""
    today = NOW.date().isoformat()
    yesterday = (NOW - timedelta(days=1)).isoformat()
    web = FakeWeb(
        {
            "https://example.com/robots.txt": robots(
                "Sitemap: https://example.com/sitemap-authors.xml\n"
                "Sitemap: https://example.com/sitemap-tags.xml\n"
                "Sitemap: https://example.com/sitemap-pages.xml\n"
                "Sitemap: https://example.com/sitemap-news.xml\n"
            ),
            HOME: html(),
            "https://example.com/sitemap-pages.xml": urlset(("https://example.com/scores", today)),
            "https://example.com/sitemap-news.xml": xml(
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
                "<url><loc>https://example.com/2026/story</loc><news:news>"
                f"<news:publication_date>{yesterday}</news:publication_date>"
                "</news:news></url></urlset>"
            ),
            "https://example.com/2026/story": html(),
            "https://example.com/scores": html(),
        }
    )
    options = settings(sitemap_max_files_per_domain=2, sitemap_max_urls_per_domain=1)
    await add_seed(session, HOME, options)
    await run(session, web, options, now=NOW)

    assert web.count("https://example.com/sitemap-news.xml") == 1
    assert web.count("https://example.com/sitemap-pages.xml") == 1
    assert web.count("https://example.com/sitemap-authors.xml") == 0
    assert web.count("https://example.com/sitemap-tags.xml") == 0
    assert web.count("https://example.com/2026/story") == 1
    assert web.count("https://example.com/scores") == 0


async def test_crawl_delay_is_obeyed(session: AsyncSession) -> None:
    starts: list[float] = []

    async def timed(request: httpx2.Request) -> httpx2.Response:
        starts.append(asyncio.get_running_loop().time())
        return html()

    pages = [f"https://slow.example/{index}" for index in range(3)]
    web = FakeWeb(dict.fromkeys(pages, timed))
    web.routes["https://slow.example/robots.txt"] = robots("User-agent: *\nCrawl-delay: 0.2")
    for page in pages:
        await add_seed(session, page, settings())
    await run(session, web, now=NOW)

    gaps = [later - earlier for earlier, later in itertools.pairwise(starts)]
    assert len(gaps) == 2
    assert all(gap >= 0.2 for gap in gaps)


async def test_a_quick_domain_speeds_up_and_keeps_its_pace(session: AsyncSession) -> None:
    pages = [f"https://quick.example/{index}" for index in range(3)]
    web = FakeWeb(dict.fromkeys(pages, html()))
    for page in pages:
        await add_seed(session, page, settings())
    pace = settings(per_domain_min_delay_s=0.01, per_domain_start_delay_s=0.2)
    await run(session, web, pace, now=NOW)

    domain = await session.scalar(
        sa.select(Domain)
        .where(Domain.host == "quick.example")
        .execution_options(populate_existing=True)
    )
    assert domain is not None
    assert domain.learned_delay_s is not None
    assert 0.01 <= domain.learned_delay_s < 0.2 / 2  # halved at least once per page


async def test_hosts_under_one_registrable_domain_share_a_gate(session: AsyncSession) -> None:
    """Blogs on one platform (`*.blogs.example`) are paced as one site; others aren't held up."""
    starts: dict[str, list[float]] = {"shared": [], "other": []}

    def timed(group: str, response: httpx2.Response) -> Route:
        async def handler(request: httpx2.Request) -> httpx2.Response:
            starts[group].append(asyncio.get_running_loop().time())
            return response

        return handler

    web = FakeWeb()
    blogs = [f"https://{name}.blogs.example/" for name in ("one", "two", "three")]
    for page in blogs:
        web.routes[page] = timed("shared", html())
        web.routes[f"{page}robots.txt"] = timed("shared", robots("User-agent: *\nAllow: /"))
    web.routes["https://other.example/"] = timed("other", html())
    for page in [*blogs, "https://other.example/"]:
        await add_seed(session, page, settings())
    pace = settings(per_domain_min_delay_s=0.1, per_domain_start_delay_s=0.1)
    await run(session, web, pace, now=NOW)

    shared = sorted(starts["shared"])
    assert len(shared) == 6  # three robots.txt files and three pages
    assert all(later - earlier >= 0.1 for earlier, later in itertools.pairwise(shared))
    assert starts["other"][0] < shared[-1]  # fetched alongside, not after them


async def test_later_rounds_fetch_what_the_last_one_found(session: AsyncSession) -> None:
    web = FakeWeb(site())
    options = settings(cycle_fetch_rounds=2)
    await add_seed(session, HOME, options, [FEED])
    reason, cycle = await run(session, web, options, now=NOW)
    assert reason is StopReason.DONE

    # The extract stage would enqueue the links it found; a link is due at once.
    found = "https://example.com/found-in-round-1"
    web.routes[found] = html()
    await add_seed(session, found, options)
    assert await start_next_round(session, cycle, options)
    reason, cycle = await run(session, web, options, now=NOW)

    assert reason is StopReason.DONE
    assert web.count(found) == 1
    assert web.count(FEED) == 1  # only the first round polls
    assert cycle.stats["fetch"]["round"] == 2  # type: ignore[index]
    assert not await start_next_round(session, cycle, options)  # CYCLE_FETCH_ROUNDS reached


async def test_a_round_stopped_early_ends_the_fetching(session: AsyncSession) -> None:
    pages = [f"https://example.com/{index}" for index in range(3)]
    web = FakeWeb({page: html() for page in pages})
    options = settings(cycle_page_budget=2)
    for page in pages:
        await add_seed(session, page, options)
    reason, cycle = await run(session, web, options, now=NOW)

    assert reason is StopReason.BUDGET
    assert not await start_next_round(session, cycle, options)


async def test_a_moved_feed_is_followed_and_updated(session: AsyncSession) -> None:
    new_feed = "https://example.com/new-feed.xml"
    web = FakeWeb(
        {
            FEED: redirect(new_feed),
            new_feed: rss("https://example.com/post"),
            "https://example.com/post": html(),
        }
    )
    await add_seed(session, HOME, settings(), [FEED])
    await run(session, web, now=NOW)
    assert web.count("https://example.com/post") == 1
    feeds = await session.scalar(
        sa.select(Domain.feed_urls)
        .where(Domain.host == "example.com")
        .execution_options(populate_existing=True)
    )
    assert feeds == [new_feed]


async def test_a_killed_stage_resumes_where_it_stopped(session: AsyncSession) -> None:
    """Kill the stage mid-request; the re-run fetches only what wasn't recorded."""
    pages = [f"https://example.com/{index}" for index in range(6)]
    blocked = asyncio.Event()
    hang = asyncio.Event()

    async def stuck(request: httpx2.Request) -> httpx2.Response:
        blocked.set()
        await hang.wait()  # never set: the request is in flight when the stage is killed
        return html()

    web = FakeWeb({page: html() for page in pages})
    # Priorities make the order deterministic; the fourth page hangs.
    for index, page in enumerate(pages):
        await add_seed(session, page, settings())
        await session.execute(
            sa.update(FrontierEntry)
            .where(FrontierEntry.url_id == (await url_row(session, page)).id)
            .values(priority=10 - index)
        )
    web.routes[pages[3]] = stuck

    task = asyncio.create_task(run(session, web, now=NOW))
    await asyncio.wait_for(blocked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.rollback()

    web.routes[pages[3]] = html()
    reason, cycle = await run(session, web, now=NOW)

    assert reason is StopReason.DONE
    assert cycle.pages_fetched == 6
    assert [web.count(page) for page in pages] == [1, 1, 1, 2, 1, 1]
    for page in pages:
        assert (await url_row(session, page)).fetch_count == 1
    # Feeds and sitemaps were polled before the kill, so the re-run skipped polling.
    assert cycle.stats["fetch"]["polled"] is True  # type: ignore[index]
    assert web.count("https://example.com/robots.txt") == 1
