import pytest

from app.enums import DocumentType
from app.ingest.classify import Classification, PageFacts, classify
from app.ingest.html import HtmlMeta

HTML = "text/html"
PDF = "application/pdf"


def page(
    url: str = "https://example.com/x", content_type: str = HTML, meta: HtmlMeta | None = None
) -> PageFacts:
    return PageFacts(url, content_type, meta or (HtmlMeta() if content_type == HTML else None))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://arxiv.org/abs/1706.03762", DocumentType.PAPER),
        ("https://arxiv.org/pdf/1706.03762v7", DocumentType.PAPER),
        ("https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1", DocumentType.PAPER),
        ("https://openreview.net/forum?id=abc", DocumentType.PAPER),
        ("https://aclanthology.org/2023.acl-long.1/", DocumentType.PAPER),
        ("https://www.govinfo.gov/content/pkg/GAOREPORTS-123/pdf/x.pdf", DocumentType.PAPER),
        ("https://www.gao.gov/assets/reports/gao-24-1.pdf", DocumentType.PAPER),
        ("https://www.nist.gov/publications/some-report", DocumentType.PAPER),
        ("https://news.ycombinator.com/item?id=1", DocumentType.THREAD),
        ("https://lobste.rs/s/abc123/a_story", DocumentType.THREAD),
        ("https://old.reddit.com/r/python/comments/abc/title/", DocumentType.THREAD),
        ("https://discuss.python.org/t/some-topic/12345", DocumentType.THREAD),
        ("https://forum.example.com/threads/why.42/", DocumentType.THREAD),
        ("https://en.wikipedia.org/wiki/Talk:PageRank", DocumentType.THREAD),
        ("https://en.wikipedia.org/wiki/User_talk:Someone", DocumentType.THREAD),
        ("https://www.youtube.com/watch?v=abc", DocumentType.VIDEO),
        ("https://youtu.be/abc", DocumentType.VIDEO),
        ("https://vimeo.com/123456", DocumentType.VIDEO),
    ],
)
def test_url_patterns(url: str, expected: DocumentType) -> None:
    assert classify(page(url)) == Classification(expected, "url")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://www.nytimes.com/topic/subject/climate",
        "https://example.gov/about",
        "https://en.wikipedia.org/wiki/Talking_Heads",
        "https://www.youtube.com/@channel",
    ],
)
def test_urls_that_say_nothing(url: str) -> None:
    assert classify(page(url)) == Classification(DocumentType.PAGE, "fallback")


def test_url_patterns_win_over_the_content_type() -> None:
    assert classify(page("https://arxiv.org/pdf/1706.03762", PDF)).type is DocumentType.PAPER
    assert classify(page("https://example.com/f.pdf", PDF)) == Classification(
        DocumentType.PDF, "content_type"
    )


def test_citation_tags_mark_papers() -> None:
    result = classify(page(meta=HtmlMeta(scholarly=True, og_type="article")))
    assert result == Classification(DocumentType.PAPER, "citation_meta")


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        ({"ScholarlyArticle"}, DocumentType.PAPER),
        ({"DiscussionForumPosting"}, DocumentType.THREAD),
        ({"QAPage"}, DocumentType.THREAD),
        ({"VideoObject"}, DocumentType.VIDEO),
        ({"BlogPosting"}, DocumentType.POST),
        ({"NewsArticle"}, DocumentType.ARTICLE),
        ({"Article"}, DocumentType.ARTICLE),
        # The most specific kind of content wins.
        ({"Article", "BlogPosting", "WebPage"}, DocumentType.POST),
        ({"Article", "VideoObject"}, DocumentType.VIDEO),
    ],
)
def test_schema_org_types(types: set[str], expected: DocumentType) -> None:
    assert classify(page(meta=HtmlMeta(schema_types=frozenset(types)))) == Classification(
        expected, "schema_org"
    )


def test_schema_org_types_that_say_nothing() -> None:
    result = classify(
        page(meta=HtmlMeta(schema_types=frozenset({"WebPage", "BreadcrumbList", "Organization"})))
    )
    assert result.type is DocumentType.PAGE


@pytest.mark.parametrize(
    ("og_type", "expected"),
    [
        ("article", DocumentType.ARTICLE),
        ("video.movie", DocumentType.VIDEO),
        ("video.other", DocumentType.VIDEO),
        ("website", DocumentType.PAGE),
        ("profile", DocumentType.PAGE),
    ],
)
def test_open_graph_types(og_type: str, expected: DocumentType) -> None:
    assert classify(page(meta=HtmlMeta(og_type=og_type))).type is expected


def test_schema_org_wins_over_open_graph() -> None:
    assert classify(
        page(meta=HtmlMeta(og_type="article", schema_types=frozenset({"BlogPosting"})))
    ).type is (DocumentType.POST)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://hacks.mozilla.org/2026/08/intent-to-ship-jpeg-xl/", DocumentType.POST),
        ("https://example.com/blog/2025/12/31/new-year", DocumentType.POST),
        ("https://example.com/2025/12/", DocumentType.PAGE),  # an archive page
        ("https://example.com/2025/12/31/", DocumentType.PAGE),
        ("https://example.com/2025/12/report?page=2", DocumentType.PAGE),
    ],
)
def test_dated_paths_look_like_blog_posts(url: str, expected: DocumentType) -> None:
    assert classify(page(url)).type is expected


def test_dated_paths_defer_to_markup() -> None:
    news = page(
        "https://news.example.com/2026/08/24/world/story.html", meta=HtmlMeta(og_type="article")
    )
    assert classify(news).type is DocumentType.ARTICLE


def test_custom_classifiers() -> None:
    class Everything:
        name = "everything"

        def classify(self, page: PageFacts) -> DocumentType | None:
            return DocumentType.VIDEO

    assert classify(page(), [Everything()]) == Classification(DocumentType.VIDEO, "everything")
    assert classify(page(), []) == Classification(DocumentType.PAGE, "fallback")
