from datetime import UTC, datetime

import pytest

from app.crawl.urls import TrackingParams
from app.enums import DocumentType
from app.ingest.extract import Analysis
from app.ingest.overrides import Override, apply_overrides

URL = "https://example.com/blog/post"
TRACKING = TrackingParams(["utm_*"])
ANALYSIS = Analysis(
    type=DocumentType.PAGE,
    classifier="fallback",
    extractor="page",
    title="Old title",
    author=None,
    published_at=None,
    language="en",
    text="text",
    excerpt="text",
    word_count=1,
    content_hash="hash",
    declared_canonical=None,
    links=(),
    noindex=False,
    nofollow=False,
)


def apply(*overrides: Override, url: str = URL) -> tuple[Analysis, list[str]]:
    return apply_overrides(ANALYSIS, url, overrides, TRACKING)


def test_no_overrides() -> None:
    assert apply() == (ANALYSIS, [])


def test_matching_overrides_replace_fields() -> None:
    result, applied = apply(
        Override("https://example.com/blog/*", "type", "post"),
        Override("*", "title", "New title"),
        Override("*", "published_at", "2026-01-02"),
        Override("*", "noindex", True),
        Override("https://other.example/*", "author", "Nobody"),
    )
    assert (result.type, result.title, result.published_at, result.noindex, result.author) == (
        DocumentType.POST,
        "New title",
        datetime(2026, 1, 2, tzinfo=UTC),
        True,
        None,
    )
    assert applied == ["type", "title", "published_at", "noindex"]


def test_later_overrides_win() -> None:
    result, _ = apply(Override("*", "title", "First"), Override("*", "title", "Second"))
    assert result.title == "Second"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/blog/c?utm_source=x", "https://example.com/blog/c"),
        ("https://www.example.com/p", "https://www.example.com/p"),
        (URL, None),  # the page itself: no declared canonical
    ],
)
def test_canonical_corrections(value: str, expected: str | None) -> None:
    result, applied = apply(Override("*", "canonical_url", value))
    assert (result.declared_canonical, applied) == (expected, ["canonical_url"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "podcast"),
        ("type", 3),
        ("title", ""),
        ("title", ["a list"]),
        ("published_at", "someday"),
        ("noindex", "yes"),
        ("canonical_url", "https://other.example/p"),  # another site
        ("canonical_url", "ftp://example.com/p"),
        ("links", []),  # not a field publishers may set
        ("word_count", 10_000),
    ],
)
def test_invalid_overrides_are_skipped(field: str, value: object) -> None:
    assert apply(Override("*", field, value)) == (ANALYSIS, [f"{field}:invalid"])
