from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx2
import pytest

from app.crawl.http import (
    FetchResult,
    Outcome,
    create_client,
    fetch,
    parse_content_type,
    parse_retry_after,
)
from app.settings import Settings
from tests.fake_web import FakeWeb, html, redirect

pytestmark = pytest.mark.anyio

URL = "https://example.com/a/page"
NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
MAX_BYTES = 1000


async def get(
    response: httpx2.Response | FakeWeb,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    web = response if isinstance(response, FakeWeb) else FakeWeb({URL: response})
    settings = Settings(_env_file=None)
    async with create_client(settings, transport=web.transport()) as client:
        return await fetch(
            client, URL, max_bytes=MAX_BYTES, now=NOW, etag=etag, last_modified=last_modified
        )


async def test_ok_page() -> None:
    result = await get(html("<p>hi</p>", etag='"v1"', **{"last-modified": "Tue, 22 Sep 2026"}))
    assert result == FetchResult(
        Outcome.OK,
        200,
        "text/html",
        None,
        b"<p>hi</p>",
        etag='"v1"',
        last_modified="Tue, 22 Sep 2026",
    )


async def test_x_robots_tag_headers_are_kept_one_per_line() -> None:
    response = httpx2.Response(
        200,
        content=b"x",
        headers=[
            ("content-type", "application/pdf"),
            ("x-robots-tag", "noindex"),
            ("x-robots-tag", "otherbot: nofollow, noarchive"),
        ],
    )
    assert (await get(response)).robots_tag == "noindex\notherbot: nofollow, noarchive"
    assert (await get(html())).robots_tag is None


async def test_identifies_itself_and_sends_validators() -> None:
    web = FakeWeb({URL: httpx2.Response(304)})
    result = await get(web, etag='"v1"', last_modified="Tue, 22 Sep 2026")
    assert result.outcome is Outcome.NOT_MODIFIED
    headers = web.requests[0].headers
    assert headers["user-agent"] == Settings(_env_file=None).user_agent
    assert headers["if-none-match"] == '"v1"'
    assert headers["if-modified-since"] == "Tue, 22 Sep 2026"


async def test_no_validators_no_conditional_headers() -> None:
    web = FakeWeb({URL: html()})
    await get(web)
    assert "if-none-match" not in web.requests[0].headers
    assert "if-modified-since" not in web.requests[0].headers


@pytest.mark.parametrize(
    ("location", "resolved"),
    [
        ("https://other.example/x", "https://other.example/x"),
        ("/moved", "https://example.com/moved"),
        ("next", "https://example.com/a/next"),
    ],
)
async def test_redirects_are_reported_not_followed(location: str, resolved: str) -> None:
    web = FakeWeb({URL: redirect(location, 302)})
    result = await get(web)
    assert (result.outcome, result.status, result.location) == (Outcome.REDIRECT, 302, resolved)
    assert not result.permanent_redirect
    assert len(web.requests) == 1


@pytest.mark.parametrize("status", [301, 308])
async def test_permanent_redirects(status: int) -> None:
    assert (await get(redirect("/new", status))).permanent_redirect


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (httpx2.Response(404), Outcome.GONE),
        (httpx2.Response(410), Outcome.GONE),
        (httpx2.Response(403), Outcome.GONE),
        (httpx2.Response(204), Outcome.GONE),
        (httpx2.Response(301), Outcome.GONE),  # a redirect without a Location
        (httpx2.Response(429), Outcome.RETRY),
        (httpx2.Response(408), Outcome.RETRY),
        (httpx2.Response(500), Outcome.RETRY),
        (httpx2.Response(503), Outcome.RETRY),
    ],
)
async def test_statuses(response: httpx2.Response, outcome: Outcome) -> None:
    assert (await get(response)).outcome is outcome


async def test_retry_after_is_reported() -> None:
    result = await get(httpx2.Response(429, headers={"retry-after": "120"}))
    assert result.retry_after_s == 120


async def test_network_errors_are_retried() -> None:
    async def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    result = await get(FakeWeb({URL: fail}))
    assert result == FetchResult(Outcome.RETRY, detail="ReadTimeout")


@pytest.mark.parametrize(
    "content_type",
    [
        "text/html; charset=utf-8",
        "application/xhtml+xml",
        "application/pdf",
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
    ],
)
async def test_allowed_content_types(content_type: str) -> None:
    response = httpx2.Response(200, content=b"x", headers={"content-type": content_type})
    assert (await get(response)).outcome is Outcome.OK


@pytest.mark.parametrize("content_type", ["image/png", "application/json", "text/plain", ""])
async def test_other_content_types_are_rejected(content_type: str) -> None:
    response = httpx2.Response(200, content=b"x", headers={"content-type": content_type})
    result = await get(response)
    assert (result.outcome, result.body) == (Outcome.REJECTED, None)


async def test_declared_size_over_the_limit_is_rejected() -> None:
    response = html("x" * (MAX_BYTES + 1))
    assert response.headers["content-length"] == str(MAX_BYTES + 1)
    assert (await get(response)).detail == "too large"


async def test_streamed_size_over_the_limit_is_rejected() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b"x" * (MAX_BYTES // 2)

    response = httpx2.Response(200, content=chunks(), headers={"content-type": "text/html"})
    assert "content-length" not in response.headers
    assert (await get(response)).detail == "too large"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, (None, None)),
        ("text/html", ("text/html", None)),
        ("Text/HTML; Charset=ISO-8859-1", ("text/html", "iso-8859-1")),
        ('text/html; boundary=x; charset="utf-8"', ("text/html", "utf-8")),
    ],
)
def test_parse_content_type(header: str | None, expected: tuple[str | None, str | None]) -> None:
    assert parse_content_type(header) == expected


@pytest.mark.parametrize(
    ("header", "seconds"),
    [
        (None, None),
        ("30", 30),
        ("Wed, 23 Sep 2026 12:05:00 GMT", 300),
        ("Wed, 23 Sep 2026 11:00:00 GMT", 0),
        ("soon", None),
    ],
)
def test_parse_retry_after(header: str | None, seconds: float | None) -> None:
    assert parse_retry_after(header, NOW) == seconds
