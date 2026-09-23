from datetime import date

import pytest

from app.enums import DocumentType
from app.ingest.extract import (
    ARTICLE,
    PAGE,
    PDF,
    VIDEO,
    Analysis,
    OutLink,
    analyze,
    extractor_for,
)
from app.settings import Settings
from tests.pages import SavedPage, saved_pages

URL = "https://example.com/blog/post"
SETTINGS = Settings(_env_file=None)
ARTICLE_BODY = " ".join(f"Sentence number {n} of a long enough article body." for n in range(80))


def run(
    head: str = "",
    body: str = f"<article><p>{ARTICLE_BODY}</p></article>",
    *,
    url: str = URL,
    robots_tag: str | None = None,
    settings: Settings = SETTINGS,
) -> Analysis:
    page = f"<html><head>{head}</head><body>{body}</body></html>".encode()
    result = analyze(url, "text/html", "utf-8", page, robots_tag, settings, product="bribot")
    assert result is not None
    return result


@pytest.mark.parametrize("page", saved_pages(), ids=lambda page: page.name)
def test_saved_pages(page: SavedPage) -> None:
    result = analyze(
        page.url,
        page.content_type,
        page.charset,
        page.body,
        page.robots_tag,
        SETTINGS,
        product="bribot",
    )
    assert result is not None
    expected = page.expected
    assert (result.type.value, result.classifier) == (expected["type"], expected["classifier"])
    assert result.title == expected["title"]
    assert result.language == expected.get("language")
    if "author" in expected:
        assert result.author == expected["author"]
    if "published" in expected:
        assert result.published_at is not None
        assert result.published_at.date() == date.fromisoformat(str(expected["published"]))
    assert result.word_count >= int(str(expected["min_words"]))
    if "contains" in expected:
        assert result.text is not None
        assert str(expected["contains"]) in result.text
    if "excerpt_start" in expected:
        assert result.excerpt is not None
        assert result.excerpt.startswith(str(expected["excerpt_start"]))
    assert len(result.links) >= int(str(expected.get("min_links", 0)))
    assert result.declared_canonical is None  # each declares its own URL as canonical
    assert not result.noindex
    assert not result.nofollow
    assert (result.content_hash is None) == (result.text is None)


def test_saved_pages_have_licenses() -> None:
    for page in saved_pages():
        assert page.license, page.name
        assert page.license != "TODO", page.name


@pytest.mark.parametrize(
    ("type_", "content_type", "expected"),
    [
        (DocumentType.ARTICLE, "text/html", ARTICLE),
        (DocumentType.POST, "text/html", ARTICLE),
        (DocumentType.PAGE, "text/html", PAGE),
        (DocumentType.VIDEO, "text/html", VIDEO),
        (DocumentType.PDF, "application/pdf", PDF),
        # No V0 extractor of their own: the fallback for the body's media type.
        (DocumentType.THREAD, "text/html", PAGE),
        (DocumentType.PAPER, "text/html", PAGE),
        (DocumentType.PAPER, "application/pdf", PDF),
        # A type whose extractor can't read the body.
        (DocumentType.ARTICLE, "application/pdf", PDF),
        (DocumentType.PAGE, "application/rss+xml", None),
    ],
)
def test_extractor_for(type_: DocumentType, content_type: str, expected: object) -> None:
    assert extractor_for(type_, content_type) is expected


@pytest.mark.parametrize(
    "content_type", ["application/rss+xml", "application/atom+xml", "application/xml", "text/xml"]
)
def test_feeds_and_xml_are_not_documents(content_type: str) -> None:
    assert analyze(URL, content_type, None, b"<rss/>", None, SETTINGS, product="bribot") is None


def test_an_empty_page_raises() -> None:
    with pytest.raises(ValueError, match="empty page"):
        analyze(URL, "text/html", None, b"", None, SETTINGS, product="bribot")


@pytest.mark.parametrize("body", [b"", b"not a pdf at all", b"%PDF-1.7\n garbage"])
def test_an_unreadable_pdf_raises(body: bytes) -> None:
    with pytest.raises(ValueError, match="PDF"):
        analyze(URL, "application/pdf", None, body, None, SETTINGS, product="bribot")


def test_text_fields() -> None:
    result = run('<title>A Post - Example</title><meta name="description" content="Summary.">')
    assert result.title == "A Post"
    assert result.excerpt == "Summary."
    assert result.text is not None
    assert result.text.startswith("Sentence number 0")
    assert result.word_count == 80 * 9
    assert result.content_hash is not None


def test_excerpt_falls_back_to_the_text() -> None:
    result = run()
    assert result.excerpt is not None
    assert result.excerpt.startswith("Sentence number 0 of")
    assert len(result.excerpt) <= SETTINGS.extract_excerpt_chars


def test_text_is_capped() -> None:
    settings = Settings(_env_file=None, extract_max_text_chars=100)
    result = run(settings=settings)
    assert result.text is not None
    assert len(result.text) == 100


def test_pages_with_little_text_still_have_metadata() -> None:
    result = run("<title>Home</title>", "")
    assert (result.type, result.title, result.text, result.word_count) == (
        DocumentType.PAGE,
        "Home",
        None,
        0,
    )
    assert result.content_hash is None


@pytest.mark.parametrize(
    ("head", "url", "expected"),
    [
        ('<link rel="canonical" href="/blog/post">', URL, None),  # itself
        ('<link rel="canonical" href="/blog/post?utm_source=x">', URL, None),  # itself, tracked
        ('<link rel="canonical" href="/blog/post">', f"{URL}?page=1", URL),
        (
            '<link rel="canonical" href="https://www.example.com/p">',
            URL,
            "https://www.example.com/p",
        ),
        ('<link rel="canonical" href="https://other.example/p">', URL, None),  # another site
        ('<link rel="canonical" href="/">', URL, None),  # the homepage misconfiguration
        ('<link rel="canonical" href="/">', "https://example.com/?ref=nav", None),
        ('<link rel="canonical" href="/">', "https://example.com/index.html", None),
        ('<meta property="og:url" content="/blog/amp">', URL, "https://example.com/blog/amp"),
        (
            '<link rel="canonical" href="https://other.example/p">'
            '<meta property="og:url" content="/blog/amp">',
            URL,
            "https://example.com/blog/amp",
        ),
        ('<link rel="canonical" href="javascript:void(0)">', URL, None),
    ],
)
def test_declared_canonical(head: str, url: str, expected: str | None) -> None:
    assert run(head, url=url).declared_canonical == expected


def test_links_are_canonical_unique_and_vouched_for() -> None:
    result = run(
        body='<p><a href="/a?utm_source=feed">First</a> <a href="/a">Again</a> '
        f'<a href="{URL}">Self</a> <a href="https://www.example.com/b">WWW</a> '
        '<a href="https://other.example/c">Other</a> '
        '<a href="https://ads.example/" rel="sponsored">Ad</a> '
        '<a href="mailto:me@example.com">Mail</a></p>'
    )
    assert result.links == (
        OutLink("https://example.com/a", "First", internal=True),
        OutLink("https://www.example.com/b", "WWW", internal=True),
        OutLink("https://other.example/c", "Other", internal=False),
    )


def test_links_and_anchors_are_capped() -> None:
    settings = Settings(_env_file=None, extract_max_links_per_page=2, extract_anchor_max_chars=3)
    result = run(body="".join(f'<a href="/p{n}">Link {n}</a>' for n in range(5)), settings=settings)
    assert [(link.url, link.anchor_text) for link in result.links] == [
        ("https://example.com/p0", "Lin"),
        ("https://example.com/p1", "Lin"),
    ]


@pytest.mark.parametrize(
    ("head", "robots_tag", "noindex", "nofollow"),
    [
        ("", None, False, False),
        ('<meta name="robots" content="noindex">', None, True, False),
        ('<meta name="robots" content="nofollow">', None, False, True),
        ('<meta name="bribot" content="none">', None, True, True),
        ('<meta name="otherbot" content="none">', None, False, False),
        ("", "noindex", True, False),
        ("", "otherbot: noindex\nbribot: nofollow", False, True),
    ],
)
def test_robots_directives(
    head: str, robots_tag: str | None, noindex: bool, nofollow: bool
) -> None:
    result = run(head, robots_tag=robots_tag)
    assert (result.noindex, result.nofollow) == (noindex, nofollow)


def test_pdf_robots_tag_applies() -> None:
    [pdf] = [page for page in saved_pages() if page.content_type == "application/pdf"]
    result = analyze(
        pdf.url, pdf.content_type, None, pdf.body, "noindex", SETTINGS, product="bribot"
    )
    assert result is not None
    assert result.noindex


def test_pdf_pages_are_capped() -> None:
    [pdf] = [page for page in saved_pages() if page.content_type == "application/pdf"]
    settings = Settings(_env_file=None, pdf_max_pages=1)
    capped = analyze(pdf.url, pdf.content_type, None, pdf.body, None, settings, product="bribot")
    full = analyze(pdf.url, pdf.content_type, None, pdf.body, None, SETTINGS, product="bribot")
    assert capped is not None
    assert full is not None
    assert 0 < capped.word_count < full.word_count
