"""The extract stage end to end: raw pages in, documents, links, frontier entries and dedup
decisions out (PLAN.md §6.1 steps 2-3, §6.3, milestone M4)."""

import hashlib
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl import frontier
from app.crawl.cycle import start_or_resume_cycle
from app.crawl.frontier import SEED, Candidate, Position
from app.db.web import (
    CrawlCycle,
    DedupDecision,
    Document,
    DomainMetadataOverride,
    FrontierEntry,
    Link,
    RawPage,
    Url,
)
from app.enums import DocumentType
from app.ingest.extract import analyze
from app.ingest.stage import ExtractStage
from app.settings import Settings
from tests.pages import saved_pages

pytestmark = pytest.mark.anyio

HOME = "https://example.com/"
POST = "https://example.com/post"
SETTINGS = Settings(_env_file=None)
NOW = datetime(2026, 9, 23, 3, tzinfo=UTC)


def article(words: str = "an article about gardens", *, head: str = "", links: str = "") -> bytes:
    body = " ".join(f"Sentence {n} is {words}." for n in range(60))
    return (
        f"<html lang='en'><head><title>Title for {words}</title>{head}</head>"
        f"<body><article><p>{body}</p>{links}</article></body></html>"
    ).encode()


async def fetched(
    session: AsyncSession,
    cycle: CrawlCycle,
    url: str,
    body: bytes,
    *,
    content_type: str = "text/html",
    robots_tag: str | None = None,
    position: Position | None = None,
) -> int:
    """What the fetch stage leaves behind for a page: its URL row with the body's hash, its
    frontier entry (at `position`, if given) and a raw page."""
    if position is not None:
        await frontier.enqueue(session, [Candidate(url, position)], SETTINGS)
    url_id = (await frontier.ensure_urls(session, [url]))[url]
    await session.execute(
        sa.update(Url)
        .where(Url.id == url_id)
        .values(content_hash=hashlib.sha256(body).hexdigest(), last_fetched_at=NOW, fetch_count=1)
    )
    session.add(
        RawPage(
            url_id=url_id,
            cycle_id=cycle.id,
            fetched_at=NOW,
            content_type=content_type,
            charset="utf-8",
            robots_tag=robots_tag,
            body=body,
        )
    )
    await session.flush()
    return url_id


async def extract(
    session: AsyncSession, cycle: CrawlCycle, settings: Settings = SETTINGS
) -> dict[str, int]:
    return dict(await ExtractStage(session, cycle, settings).run())


@pytest.fixture
async def cycle(session: AsyncSession) -> CrawlCycle:
    return await start_or_resume_cycle(session, SETTINGS)


async def document_of(session: AsyncSession, url: str) -> Document | None:
    document_id = await session.scalar(sa.select(Url.document_id).where(Url.url == url))
    if document_id is None:
        return None
    return await session.get(Document, document_id, populate_existing=True)


async def position_of(session: AsyncSession, url: str) -> Position | None:
    row = (
        await session.execute(
            sa.select(FrontierEntry.internal_depth, FrontierEntry.external_hops)
            .join(Url, Url.id == FrontierEntry.url_id)
            .where(Url.url == url)
        )
    ).one_or_none()
    return Position(*row) if row else None


async def links_of(session: AsyncSession, document_id: int) -> list[tuple[str, str | None, bool]]:
    rows = await session.execute(
        sa.select(Url.url, Link.anchor_text, Link.is_internal)
        .join(Url, Url.id == Link.dst_url_id)
        .where(Link.src_document_id == document_id)
        .order_by(Url.url)
    )
    return list(rows.tuples())


async def decisions(session: AsyncSession) -> list[tuple[str, str]]:
    rows = await session.execute(
        sa.select(Url.url, DedupDecision.method)
        .join(Url, Url.id == DedupDecision.url_id)
        .order_by(DedupDecision.id)
    )
    return list(rows.tuples())


async def test_a_page_becomes_a_document_and_its_links_are_followed(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    links = (
        '<a href="/a?utm_source=x">Internal</a> <a href="https://other.example/b">External</a>'
        '<a href="https://ads.example/" rel="sponsored">Ad</a>'
    )
    await fetched(session, cycle, HOME, article(links=links), position=SEED)

    counts = await extract(session, cycle)

    document = await document_of(session, HOME)
    assert document is not None
    assert (document.type, document.title, document.language) == (
        DocumentType.PAGE,
        "Title for an article about gardens",
        "en",
    )
    assert document.text is not None
    assert document.text.startswith("Sentence 0 is")
    assert document.word_count is not None
    assert document.word_count >= 60 * 7  # the text, plus its links' anchors
    assert document.content_hash is not None
    assert await links_of(session, document.id) == [
        ("https://example.com/a", "Internal", True),
        ("https://other.example/b", "External", False),
    ]
    # One step further from the seed: a click within the site, a hop to another.
    assert await position_of(session, "https://example.com/a") == Position(1, 0)
    assert await position_of(session, "https://other.example/b") == Position(0, 1)
    assert await position_of(session, "https://ads.example/") is None
    assert await decisions(session) == [(HOME, "new")]
    assert await session.scalar(sa.select(sa.func.count()).select_from(RawPage)) == 0
    assert counts["pages"] == 1
    assert counts["documents.new"] == 1
    assert counts["enqueued"] == 2
    assert counts["links"] == 2
    stats = cycle.stats["extract"]
    assert isinstance(stats, dict)
    assert stats["counts"] == counts


async def test_links_beyond_the_limits_are_edges_but_not_enqueued(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    settings = Settings(_env_file=None, max_external_hops=0)
    await fetched(
        session,
        cycle,
        HOME,
        article(links='<a href="https://other.example/b">x</a>'),
        position=SEED,
    )
    await extract(session, cycle, settings)
    document = await document_of(session, HOME)
    assert document is not None
    assert [url for url, _, _ in await links_of(session, document.id)] == [
        "https://other.example/b"
    ]
    assert await position_of(session, "https://other.example/b") is None


async def test_a_changed_page_updates_its_document(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    await fetched(session, cycle, POST, article("roses", links='<a href="/old">o</a>'))
    await extract(session, cycle)
    first = await document_of(session, POST)
    assert first is not None

    await fetched(session, cycle, POST, article("tulips", links='<a href="/new">n</a>'))
    await extract(session, cycle)
    second = await document_of(session, POST)
    assert second is not None
    assert second.id == first.id
    assert second.title == "Title for tulips"
    assert [url for url, _, _ in await links_of(session, second.id)] == ["https://example.com/new"]
    # The URL's document didn't change, so there's nothing new to log.
    assert await decisions(session) == [(POST, "new")]


async def test_a_declared_canonical_owns_the_document(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    amp = "https://example.com/post/amp"
    canonical = f'<link rel="canonical" href="{POST}">'
    await fetched(session, cycle, amp, article("amp", head=canonical), position=Position(1, 0))
    await extract(session, cycle)

    document = await document_of(session, amp)
    assert document is not None
    assert await document_of(session, POST) == document
    canonical_id = await session.scalar(sa.select(Url.id).where(Url.url == POST))
    assert document.canonical_url_id == canonical_id
    assert document.title == "Title for amp"  # the canonical URL hasn't been fetched yet
    # The canonical URL is enqueued at the page's own distance from the seeds.
    assert await position_of(session, POST) == Position(1, 0)
    assert await decisions(session) == [(amp, "new"), (POST, "new")]

    # Once fetched, the canonical URL is the document's content source...
    await fetched(session, cycle, POST, article("canonical"))
    counts = await extract(session, cycle)
    assert counts["dedup.canonical_url"] == 1
    document = await document_of(session, POST)
    assert document is not None
    assert document.title == "Title for canonical"
    # ...and a later change to the duplicate doesn't overwrite it.
    await fetched(session, cycle, amp, article("amp again", head=canonical))
    await extract(session, cycle)
    document = await document_of(session, amp)
    assert document is not None
    assert document.title == "Title for canonical"
    assert len(await decisions(session)) == 2


async def test_exact_duplicates_share_a_document(session: AsyncSession, cycle: CrawlCycle) -> None:
    original, copy = "https://blog.example/essay", "https://mirror.example/essay"
    await fetched(session, cycle, original, article("syndicated"))
    await extract(session, cycle)
    # Different markup and title, same text.
    body = article("syndicated").replace(b"Title for", b"Mirrored:")
    await fetched(session, cycle, copy, body)
    counts = await extract(session, cycle)

    document = await document_of(session, copy)
    assert document is not None
    assert document == await document_of(session, original)
    assert document.title == "Title for syndicated"  # a duplicate never overwrites
    assert counts["dedup.content_hash"] == 1
    assert await decisions(session) == [(original, "new"), (copy, "content_hash")]


async def test_short_texts_never_match_by_hash(session: AsyncSession, cycle: CrawlCycle) -> None:
    page = b"<html><body><article><p>Page not found. Sorry about that.</p></article></body></html>"
    await fetched(session, cycle, "https://a.example/missing", page)
    await fetched(session, cycle, "https://b.example/missing", page)
    await extract(session, cycle)
    first = await document_of(session, "https://a.example/missing")
    second = await document_of(session, "https://b.example/missing")
    assert first is not None
    assert second is not None
    assert first.id != second.id


async def test_noindex_pages_are_followed_but_not_documents(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    body = article(head='<meta name="robots" content="noindex">', links='<a href="/a">a</a>')
    await fetched(session, cycle, HOME, body, position=SEED)
    counts = await extract(session, cycle)
    assert await document_of(session, HOME) is None
    assert await position_of(session, "https://example.com/a") == Position(1, 0)
    assert counts["noindex"] == 1
    assert await session.scalar(sa.select(sa.func.count()).select_from(Document)) == 0


async def test_x_robots_tag_noindex(session: AsyncSession, cycle: CrawlCycle) -> None:
    await fetched(session, cycle, HOME, article(), robots_tag="bribot: noindex")
    await extract(session, cycle)
    assert await document_of(session, HOME) is None


async def test_nofollow_pages_are_documents_without_links(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    body = article(head='<meta name="robots" content="nofollow">', links='<a href="/a">a</a>')
    await fetched(session, cycle, HOME, body, position=SEED)
    await extract(session, cycle)
    document = await document_of(session, HOME)
    assert document is not None
    assert await links_of(session, document.id) == []
    assert await position_of(session, "https://example.com/a") is None


async def test_redirects_take_their_targets_document(
    session: AsyncSession, cycle: CrawlCycle
) -> None:
    old, middle = "https://example.com/old", "https://example.com/middle"
    ids = await frontier.ensure_urls(session, [old, middle, POST])
    await session.execute(
        sa.update(Url).where(Url.id == ids[old]).values(redirect_to_url_id=ids[middle])
    )
    await session.execute(
        sa.update(Url).where(Url.id == ids[middle]).values(redirect_to_url_id=ids[POST])
    )
    await fetched(session, cycle, POST, article())
    counts = await extract(session, cycle)
    document = await document_of(session, POST)
    assert document is not None
    assert await document_of(session, old) == document
    assert await document_of(session, middle) == document
    assert counts["dedup.redirect"] == 2
    assert sorted(await decisions(session)) == [
        (middle, "redirect"),
        (old, "redirect"),
        (POST, "new"),
    ]


async def test_other_bodies_are_dropped(session: AsyncSession, cycle: CrawlCycle) -> None:
    await fetched(
        session, cycle, "https://example.com/feed", b"<rss/>", content_type="application/rss+xml"
    )
    await fetched(session, cycle, "https://example.com/empty", b"")
    await fetched(
        session,
        cycle,
        "https://example.com/f.pdf",
        b"%PDF-1.7 junk",
        content_type="application/pdf",
    )
    counts = await extract(session, cycle)
    assert (counts["not_a_document"], counts["failed"]) == (1, 2)
    assert await session.scalar(sa.select(sa.func.count()).select_from(Document)) == 0
    assert await session.scalar(sa.select(sa.func.count()).select_from(RawPage)) == 0


async def test_domain_overrides_apply(session: AsyncSession, cycle: CrawlCycle) -> None:
    url_id = await fetched(session, cycle, POST, article())
    domain_id = await session.scalar(sa.select(Url.domain_id).where(Url.id == url_id))
    assert domain_id is not None
    session.add(
        DomainMetadataOverride(
            domain_id=domain_id, url_pattern="https://example.com/*", field="type", value="post"
        )
    )
    await session.flush()
    counts = await extract(session, cycle)
    document = await document_of(session, POST)
    assert document is not None
    assert document.type is DocumentType.POST
    assert counts["override.type"] == 1


async def test_saved_pages_become_documents(session: AsyncSession, cycle: CrawlCycle) -> None:
    pages = saved_pages()
    for page in pages:
        await fetched(
            session, cycle, page.url, page.body, content_type=page.content_type, position=SEED
        )
    counts = await extract(session, cycle)
    assert counts["documents.new"] == len(pages)
    for page in pages:
        document = await document_of(session, page.url)
        assert document is not None, page.name
        assert (document.type.value, document.title) == (
            page.expected["type"],
            page.expected["title"],
        ), page.name
    assert counts["enqueued"] > 0


class Killed(BaseException):
    """Stands in for a kill: like SIGKILL, nothing in the stage handles it."""


async def test_a_killed_stage_resumes_with_the_pages_left(
    session: AsyncSession, cycle: CrawlCycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fetched(session, cycle, "https://example.com/1", article("one"))
    await fetched(session, cycle, "https://example.com/2", article("two"))
    calls = 0

    def killed_on_the_second_page(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Killed
        return analyze(*args, **kwargs)  # type: ignore[arg-type]  # passes through

    monkeypatch.setattr("app.ingest.stage.analyze", killed_on_the_second_page)
    with pytest.raises(Killed):
        await extract(session, cycle)
    await session.rollback()
    assert await document_of(session, "https://example.com/1") is not None
    assert await session.scalar(sa.select(sa.func.count()).select_from(RawPage)) == 1

    monkeypatch.undo()
    resumed = await start_or_resume_cycle(session, SETTINGS)
    counts = await extract(session, resumed)
    assert await document_of(session, "https://example.com/2") is not None
    assert counts["documents.new"] == 2  # counted across both runs
