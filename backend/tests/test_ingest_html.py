from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.ingest.html import (
    HtmlMeta,
    decode,
    extract_meta,
    parse_datetime,
    parse_document,
    parse_robots_directives,
    strip_site_name,
)

URL = "https://example.com/blog/post"


def meta_of(head: str, body: str = "", *, url: str = URL, lang: str = "en") -> HtmlMeta:
    page = f'<html lang="{lang}"><head>{head}</head><body>{body}</body></html>'
    return extract_meta(parse_document(page.encode(), "utf-8"), url, "bribot")


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], set()),
        (["noindex, nofollow"], {"noindex", "nofollow"}),
        (["NoIndex"], {"noindex"}),
        (["none"], {"noindex", "nofollow"}),
        (
            ["index, follow, max-image-preview:large"],
            {"index", "follow", "max-image-preview:large"},
        ),
        (["max-snippet: 20"], {"max-snippet: 20"}),
        (
            ["unavailable_after: 25 Jun 2010 15:00:00 PST"],
            {"unavailable_after: 25 jun 2010 15:00:00 pst"},
        ),
        # Addressed to one user agent: only ours counts.
        (["googlebot: noindex"], set()),
        (["bribot: noindex"], {"noindex"}),
        (["BriBot: nofollow", "otherbot: noindex"], {"nofollow"}),
    ],
)
def test_parse_robots_directives(values: list[str], expected: set[str]) -> None:
    assert parse_robots_directives(values, "bribot") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("2026-09-23", datetime(2026, 9, 23, tzinfo=UTC)),
        ("2017/06/12", datetime(2017, 6, 12, tzinfo=UTC)),
        ("2001-11-05T06:06:13Z", datetime(2001, 11, 5, 6, 6, 13, tzinfo=UTC)),
        (
            "2026-08-24T08:32:15-07:00",
            datetime(2026, 8, 24, 8, 32, 15, tzinfo=timezone(timedelta(hours=-7))),
        ),
        ("2026-08-24T08:32:15", datetime(2026, 8, 24, 8, 32, 15, tzinfo=UTC)),
        ("2026-08-24T08:32:15.123+0000 extra", datetime(2026, 8, 24, tzinfo=UTC)),
        ("last Tuesday", None),
    ],
)
def test_parse_datetime(value: str | None, expected: datetime | None) -> None:
    assert parse_datetime(value) == expected


@pytest.mark.parametrize(
    ("title", "site_name", "url", "expected"),
    [
        ("PageRank - Wikipedia", None, "https://en.wikipedia.org/wiki/PageRank", "PageRank"),
        (
            "Talk:PageRank - Wikipedia",
            None,
            "https://en.wikipedia.org/wiki/Talk:P",
            "Talk:PageRank",
        ),
        (
            "Intent to Ship: JPEG XL \u2013 Mozilla Hacks - the Web developer blog",
            "Mozilla Hacks \u2013 the Web developer blog",
            "https://hacks.mozilla.org/x",
            "Intent to Ship: JPEG XL",
        ),
        ("A title | The Site", "The Site", "https://site.example/x", "A title"),
        (
            "What is Free Software? - GNU Project - FSF",
            None,
            "https://www.gnu.org/x",
            "What is Free Software?",
        ),
        # A separator inside the title itself stays.
        (
            "Rust vs. Go - a comparison",
            None,
            "https://blog.example/x",
            "Rust vs. Go - a comparison",
        ),
        ("Hyphen-ated title", "Site", "https://site.example/x", "Hyphen-ated title"),
        # The site name alone is the title.
        ("Wikipedia", None, "https://en.wikipedia.org/", "Wikipedia"),
        # Short host labels such as "en" aren't recognized.
        ("Something - En passant", None, "https://en.example.org/x", "Something - En passant"),
    ],
)
def test_strip_site_name(title: str, site_name: str | None, url: str, expected: str) -> None:
    assert strip_site_name(title, site_name, url) == expected


@pytest.mark.parametrize(
    ("body", "charset", "expected"),
    [
        ("café".encode(), "utf-8", "café"),
        ("café".encode("latin-1"), "iso-8859-1", "café"),
        (
            '<meta charset="iso-8859-1">café'.encode("latin-1"),
            None,
            '<meta charset="iso-8859-1">café',
        ),
        ("café".encode(), None, "café"),
        ("café".encode(), "no-such-charset", "café"),
        (b"caf\xff", "utf-8", "caf�"),
    ],
)
def test_decode(body: bytes, charset: str | None, expected: str) -> None:
    assert decode(body, charset) == expected


@pytest.mark.parametrize("body", [b"", b"   \n"])
def test_parse_document_rejects_empty_pages(body: bytes) -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_document(body, None)


def test_parse_document_accepts_an_xml_declaration() -> None:
    page = b'<?xml version="1.0" encoding="utf-8"?><html><head><title>X</title></head></html>'
    assert parse_document(page, None).findtext(".//title") == "X"


def test_titles_prefer_open_graph_then_citation_then_title_element() -> None:
    assert meta_of('<title>T</title><meta property="og:title" content="OG">').title == "OG"
    assert meta_of('<title>T</title><meta name="citation_title" content="C">').title == "C"
    assert meta_of("<title>  Plain   title </title>").title == "Plain title"
    assert meta_of("").title is None


def test_open_graph_and_description() -> None:
    meta = meta_of(
        '<meta property="og:type" content="article">'
        '<meta name="description" content="From the meta tag">'
        '<meta property="og:url" content="/canonical">'
    )
    assert (meta.og_type, meta.description, meta.og_url) == (
        "article",
        "From the meta tag",
        "https://example.com/canonical",
    )
    assert meta_of('<meta property="og:description" content="OG">').description == "OG"


def test_canonical_is_resolved_against_the_base_url() -> None:
    meta = meta_of('<base href="https://example.com/other/"><link rel="canonical" href="page">')
    assert meta.canonical == "https://example.com/other/page"
    assert meta_of('<link rel="Canonical" href="/x">').canonical == "https://example.com/x"
    assert meta_of('<link rel="alternate" href="/x">').canonical is None


@pytest.mark.parametrize(
    ("lang", "expected"),
    [("en", "en"), ("en-US", "en"), ("PT-br", "pt"), ("", None), ("not a language", None)],
)
def test_language(lang: str, expected: str | None) -> None:
    assert meta_of("", lang=lang).language == expected


def test_json_ld_types_author_and_date() -> None:
    meta = meta_of(
        '<script type="application/ld+json">'
        '{"@context": "https://schema.org", "@graph": ['
        '{"@type": "WebPage"}, '
        '{"@type": ["BlogPosting", "https://schema.org/Article"], '
        '"author": [{"@type": "Person", "name": "Ada Lovelace"}], '
        '"datePublished": "2026-01-02T03:04:05Z"}]}'
        "</script>"
        '<script type="application/ld+json">not json</script>'
    )
    assert meta.schema_types == {"WebPage", "BlogPosting", "Article"}
    assert meta.author == "Ada Lovelace"
    assert meta.published_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_microdata_types_are_top_level_items_only() -> None:
    meta = meta_of(
        "",
        '<div itemscope itemtype="http://schema.org/DiscussionForumPosting">'
        '<span itemscope itemtype="http://schema.org/Person">x</span></div>',
    )
    assert meta.schema_types == {"DiscussionForumPosting"}


def test_author_and_date_fall_back_to_meta_tags() -> None:
    meta = meta_of(
        '<meta name="citation_author" content="Vaswani, Ashish">'
        '<meta name="citation_date" content="2017/06/12">'
    )
    assert meta.author == "Vaswani, Ashish"
    assert meta.published_at == datetime(2017, 6, 12, tzinfo=UTC)
    assert meta_of('<meta name="citation_title" content="T">').scholarly


def test_robots_meta_tags_for_everyone_and_for_us() -> None:
    assert meta_of('<meta name="robots" content="noindex">').robots == {"noindex"}
    assert meta_of('<meta name="bribot" content="nofollow">').robots == {"nofollow"}
    assert meta_of('<meta name="googlebot" content="noindex">').robots == set()


def test_links() -> None:
    meta = meta_of(
        "",
        '<a href="/a">A <b>link</b></a>'
        '<a href="#top">skip</a>'
        '<a href="https://other.example/x" rel="nofollow">paid</a>'
        '<a href="b" rel="UGC noopener">comment</a>'
        "<a>no href</a>",
    )
    assert [(link.url, link.text, link.nofollow) for link in meta.links] == [
        ("https://example.com/a", "A link", False),
        ("https://other.example/x", "paid", True),
        ("https://example.com/blog/b", "comment", True),
    ]
