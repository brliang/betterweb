import gzip
from datetime import UTC, datetime, timedelta

import pytest

from app.crawl.sources import (
    SourceItem,
    gunzip,
    newest,
    order_sitemaps,
    parse_feed,
    parse_lastmod,
    parse_sitemap,
)

NOW = datetime(2026, 9, 23, tzinfo=UTC)
MAX_BYTES = 100_000

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Blog</title>
<item><title>One</title><link>https://example.com/one</link>
<pubDate>Mon, 21 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>No date</title><link>https://example.com/two</link></item>
<item><title>No link</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>Relative</title><link href="/posts/relative"/>
<updated>2026-09-22T08:00:00Z</updated></entry>
</feed>"""

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.com/a</loc><lastmod>2026-09-20</lastmod></url>
<url><loc> https://example.com/b </loc></url>
<url><lastmod>2026-09-20</lastmod></url>
</urlset>"""

INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://example.com/sitemap-1.xml</loc><lastmod>2026-09-01T00:00:00+02:00</lastmod></sitemap>
</sitemapindex>"""

NEWS = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
  xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://example.com/2026/09/22/story</loc><lastmod>2026-09-23T09:00:00Z</lastmod>
<news:news><news:publication><news:name>Ex</news:name><news:language>en</news:language>
</news:publication><news:publication_date>2026-09-22T12:00:00-04:00</news:publication_date>
<news:title>Story</news:title></news:news></url>
</urlset>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<urlset><url><loc>&lol2;</loc></url></urlset>"""


def test_rss_feed() -> None:
    assert parse_feed(RSS, "https://example.com/feed.xml") == [
        SourceItem("https://example.com/one", datetime(2026, 9, 21, 10, tzinfo=UTC)),
        SourceItem("https://example.com/two"),
    ]


def test_atom_feed_resolves_relative_links() -> None:
    assert parse_feed(ATOM, "https://example.com/atom.xml") == [
        SourceItem("https://example.com/posts/relative", datetime(2026, 9, 22, 8, tzinfo=UTC)),
    ]


def test_garbage_is_an_empty_feed() -> None:
    assert parse_feed(b"<html>not a feed</html>", "https://example.com/") == []


def test_urlset() -> None:
    sitemap = parse_sitemap(URLSET, MAX_BYTES)
    assert sitemap is not None
    assert not sitemap.is_index
    assert sitemap.items == [
        SourceItem("https://example.com/a", datetime(2026, 9, 20, tzinfo=UTC)),
        SourceItem("https://example.com/b"),
    ]


def test_sitemap_index() -> None:
    sitemap = parse_sitemap(INDEX, MAX_BYTES)
    assert sitemap is not None
    assert sitemap.is_index
    assert sitemap.items == [
        SourceItem("https://example.com/sitemap-1.xml", datetime(2026, 8, 31, 22, tzinfo=UTC))
    ]


def test_gzipped_sitemap() -> None:
    sitemap = parse_sitemap(gzip.compress(URLSET), MAX_BYTES)
    assert sitemap is not None
    assert len(sitemap.items) == 2


@pytest.mark.parametrize(
    "content",
    [b"not xml", b"<html><body/></html>", BILLION_LAUGHS, gzip.compress(URLSET)[:20]],
)
def test_invalid_sitemaps(content: bytes) -> None:
    assert parse_sitemap(content, MAX_BYTES) is None


def test_gzip_bombs_are_refused() -> None:
    bomb = gzip.compress(b"<" * (MAX_BYTES * 10))
    assert len(bomb) < MAX_BYTES
    assert gunzip(bomb, MAX_BYTES) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-01", datetime(2026, 9, 1, tzinfo=UTC)),
        ("2026-09-01T10:00:00Z", datetime(2026, 9, 1, 10, tzinfo=UTC)),
        ("2026-09-01T10:00:00+02:00", datetime(2026, 9, 1, 8, tzinfo=UTC)),
        ("2026-09-01T10:00", datetime(2026, 9, 1, 10, tzinfo=UTC)),
        ("September 1st", None),
        (None, None),
    ],
)
def test_parse_lastmod(text: str | None, expected: datetime | None) -> None:
    assert parse_lastmod(text) == expected


def test_newest_first_undated_last_old_dropped() -> None:
    def item(name: str, days_ago: int | None) -> SourceItem:
        updated = None if days_ago is None else NOW - timedelta(days=days_ago)
        return SourceItem(f"https://example.com/{name}", updated)

    items = [item("old", 40), item("undated", None), item("recent", 1), item("newer", 0)]
    chosen = newest(items, 10, now=NOW, max_age=timedelta(days=30))
    assert [i.url.rsplit("/", 1)[1] for i in chosen] == ["newer", "recent", "undated"]
    assert len(newest(items, 2, now=NOW)) == 2


def test_news_sitemap_entries_have_a_publication_date() -> None:
    sitemap = parse_sitemap(NEWS, MAX_BYTES)
    assert sitemap is not None
    assert sitemap.items == [
        SourceItem(
            "https://example.com/2026/09/22/story",
            updated=datetime(2026, 9, 23, 9, tzinfo=UTC),
            published=datetime(2026, 9, 22, 16, tzinfo=UTC),
        )
    ]


def test_published_items_go_before_merely_updated_ones() -> None:
    """A stats page touched a minute ago doesn't crowd out yesterday's article."""
    stats = SourceItem("https://example.com/stats", updated=NOW)
    article = SourceItem(
        "https://example.com/article",
        updated=NOW - timedelta(days=2),
        published=NOW - timedelta(days=1),
    )
    old = SourceItem("https://example.com/old", published=NOW - timedelta(days=40))
    chosen = newest([stats, old, article], 10, now=NOW, max_age=timedelta(days=30))
    assert chosen == [article, stats]
    assert newest([stats, article], 1, now=NOW) == [article]


def test_sitemaps_are_ordered_news_first_and_hubs_are_skipped() -> None:
    urls = [
        "https://example.com/athletic/sitemap-authors.xml",
        "https://example.com/athletic/sitemap-players.xml",
        "https://example.com/sitemaps/new/sitemap.xml.gz",
        "https://example.com/tags/sitemap.xml",
        "https://example.com/post-sitemap.xml",
        "https://example.com/sitemaps/new/news.xml.gz",
        "https://example.com/feed/google-news-sitemap-feed/sitemap-google-news",
    ]
    assert order_sitemaps(urls, ["authors", "players", "tags"]) == [
        "https://example.com/sitemaps/new/news.xml.gz",
        "https://example.com/feed/google-news-sitemap-feed/sitemap-google-news",
        "https://example.com/sitemaps/new/sitemap.xml.gz",
        "https://example.com/post-sitemap.xml",
    ]


def test_skip_words_match_whole_words_only() -> None:
    urls = ["https://example.com/sitemap-contagion.xml", "https://example.com/sitemap-TAG.xml"]
    assert order_sitemaps(urls, ["tag"]) == ["https://example.com/sitemap-contagion.xml"]
