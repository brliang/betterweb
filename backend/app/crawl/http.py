"""One HTTP request for the crawler, classified into what the frontier should do next.

Redirects are not followed: a redirect is recorded and its target goes through the frontier
like any other URL, so robots.txt, depth limits and per-domain delays apply to it too.
"""

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from urllib.parse import urljoin

import httpx2

from app.crawl.robots import MAX_ROBOTS_REDIRECTS
from app.settings import Settings

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "application/pdf",
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
    }
)
"""The content-type allowlist (PLAN.md §6.2): pages, PDFs, feeds and sitemaps."""
ACCEPT = ", ".join([*sorted(ALLOWED_CONTENT_TYPES), "*/*;q=0.1"])
PERMANENT_REDIRECTS = frozenset({301, 308})
REDIRECTS = frozenset({301, 302, 303, 307, 308})
RETRYABLE = frozenset({408, 425, 429})
"""4xx statuses that mean "try again later"; every 5xx is retryable too."""


class Outcome(StrEnum):
    OK = "ok"
    NOT_MODIFIED = "not_modified"
    REDIRECT = "redirect"
    GONE = "gone"
    """A 4xx (or other final answer): don't fetch this URL again."""
    RETRY = "retry"
    """429, 5xx, a timeout or a network error: try again later, and slow down on this domain."""
    REJECTED = "rejected"
    """Not on the content-type allowlist, or larger than MAX_PAGE_BYTES."""


@dataclass(frozen=True)
class FetchResult:
    outcome: Outcome
    status: int | None = None
    """None when no HTTP response arrived."""
    content_type: str | None = None
    charset: str | None = None
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    location: str | None = None
    """For a redirect: the target, resolved against the request URL (not canonicalized)."""
    retry_after_s: float | None = None
    robots_tag: str | None = None
    """X-Robots-Tag headers, one per line (each may address a user agent)."""
    detail: str | None = None
    """Why the fetch was rejected or failed, for logs and stats."""

    @property
    def permanent_redirect(self) -> bool:
        return self.status in PERMANENT_REDIRECTS


def create_client(
    settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        headers={"User-Agent": settings.user_agent, "Accept": ACCEPT},
        timeout=settings.fetch_timeout_s,
        follow_redirects=False,
        max_redirects=MAX_ROBOTS_REDIRECTS,
        limits=httpx2.Limits(max_connections=settings.global_concurrency),
        transport=transport,
    )


def parse_content_type(header: str | None) -> tuple[str | None, str | None]:
    """`text/html; charset=UTF-8` -> ("text/html", "utf-8")."""
    if not header:
        return None, None
    media_type, *params = (part.strip() for part in header.split(";"))
    charset = None
    for param in params:
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"').lower() or None
    return media_type.lower() or None, charset


def parse_retry_after(header: str | None, now: datetime) -> float | None:
    """Seconds to wait from a Retry-After header: delay-seconds or an HTTP date."""
    if not header:
        return None
    if header.strip().isdigit():
        return float(header.strip())
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - now).total_seconds())


async def fetch(
    client: httpx2.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    now: datetime,
    etag: str | None = None,
    last_modified: str | None = None,
    allowed_types: frozenset[str] = ALLOWED_CONTENT_TYPES,
) -> FetchResult:
    """GET `url`, conditionally when a validator from the last fetch is known."""
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        async with client.stream("GET", url, headers=headers) as response:
            return await _classify(response, url, max_bytes, now, allowed_types)
    except httpx2.RequestError as error:
        return FetchResult(Outcome.RETRY, detail=type(error).__name__)


async def _classify(
    response: httpx2.Response,
    url: str,
    max_bytes: int,
    now: datetime,
    allowed_types: frozenset[str],
) -> FetchResult:
    status = response.status_code
    etag = response.headers.get("etag")
    last_modified = response.headers.get("last-modified")
    if status == 304:
        return FetchResult(Outcome.NOT_MODIFIED, status, etag=etag, last_modified=last_modified)
    if status in REDIRECTS:
        location = response.headers.get("location")
        if not location:
            return FetchResult(Outcome.GONE, status, detail="redirect without Location")
        return FetchResult(Outcome.REDIRECT, status, location=urljoin(url, location.strip()))
    if status in RETRYABLE or status >= 500:
        retry_after = parse_retry_after(response.headers.get("retry-after"), now)
        return FetchResult(Outcome.RETRY, status, retry_after_s=retry_after, detail=f"{status}")
    if status not in (200, 203):
        return FetchResult(Outcome.GONE, status, detail=f"{status}")

    content_type, charset = parse_content_type(response.headers.get("content-type"))
    if content_type not in allowed_types:
        return FetchResult(Outcome.REJECTED, status, content_type, detail="content type")
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        return FetchResult(Outcome.REJECTED, status, content_type, detail="too large")
    chunks = []
    size = 0
    async for chunk in response.aiter_bytes():  # decompressed, so size limits are real
        size += len(chunk)
        if size > max_bytes:
            return FetchResult(Outcome.REJECTED, status, content_type, detail="too large")
        chunks.append(chunk)
    return FetchResult(
        Outcome.OK,
        status,
        content_type,
        charset,
        b"".join(chunks),
        etag=etag,
        last_modified=last_modified,
        robots_tag="\n".join(response.headers.get_list("x-robots-tag")) or None,
    )
