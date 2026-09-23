"""Parsing feeds and sitemaps into URLs to enqueue (PLAN.md §6.1 step 1.1)."""

import time
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from xml.etree.ElementTree import Element, ParseError

import feedparser  # type: ignore[import-untyped]  # ships no type information
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

GZIP_MAGIC = b"\x1f\x8b"
SITEMAP_EXTRA_TYPES = frozenset({"application/gzip", "application/x-gzip"})
"""Sitemaps may be served gzipped (`sitemap.xml.gz`) on top of the usual XML types."""


@dataclass(frozen=True)
class SourceItem:
    url: str
    """Absolute, not yet canonicalized."""
    updated: datetime | None = None


@dataclass(frozen=True)
class Sitemap:
    is_index: bool
    """An index lists child sitemaps; otherwise `items` are page URLs."""
    items: list[SourceItem]


def parse_feed(content: bytes, base_url: str) -> list[SourceItem]:
    """The entries of an RSS or Atom feed; relative links resolve against `base_url`."""
    parsed = feedparser.parse(content, response_headers={"content-location": base_url})
    items = []
    for entry in parsed.entries:
        link = entry.get("link")
        if isinstance(link, str) and link:
            stamp = entry.get("published_parsed") or entry.get("updated_parsed")
            updated = (
                datetime(*stamp[:6], tzinfo=UTC) if isinstance(stamp, time.struct_time) else None
            )
            items.append(SourceItem(link, updated))
    return items


def _local_name(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child_text(element: Element, name: str) -> str | None:
    for child in element:
        if _local_name(child) == name and child.text:
            return child.text.strip()
    return None


def parse_lastmod(text: str | None) -> datetime | None:
    """A W3C datetime (`2026-09-01`, `2026-09-01T10:00:00+02:00`), as UTC."""
    if not text:
        return None
    try:
        if len(text) == len("2026-09-01"):
            return datetime.combine(date.fromisoformat(text), datetime.min.time(), UTC)
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def gunzip(content: bytes, max_bytes: int) -> bytes | None:
    """Decompress a gzipped body, or None if it is corrupt or inflates past `max_bytes`."""
    decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)
    try:
        data = decompressor.decompress(content, max_bytes + 1)
    except zlib.error:
        return None
    return data if len(data) <= max_bytes else None


def parse_sitemap(content: bytes, max_bytes: int) -> Sitemap | None:
    """A sitemap or sitemap index (sitemaps.org), gzipped or not; None if it isn't one."""
    if content.startswith(GZIP_MAGIC):
        unzipped = gunzip(content, max_bytes)
        if unzipped is None:
            return None
        content = unzipped
    try:
        root = fromstring(content)
    except (ParseError, DefusedXmlException):
        return None
    kind = _local_name(root)
    if kind not in ("urlset", "sitemapindex"):
        return None
    child = "url" if kind == "urlset" else "sitemap"
    items = [
        SourceItem(loc, parse_lastmod(_child_text(element, "lastmod")))
        for element in root
        if _local_name(element) == child and (loc := _child_text(element, "loc"))
    ]
    return Sitemap(is_index=kind == "sitemapindex", items=items)


def newest(
    items: list[SourceItem], limit: int, *, now: datetime, max_age: timedelta | None = None
) -> list[SourceItem]:
    """Up to `limit` items, newest first, undated ones last; dated ones older than `max_age`
    are dropped."""
    cutoff = now - max_age if max_age is not None else None
    dated = sorted(
        (item for item in items if item.updated and (cutoff is None or item.updated >= cutoff)),
        key=lambda item: item.updated or now,
        reverse=True,
    )
    undated = [item for item in items if item.updated is None]
    return (dated + undated)[:limit]
