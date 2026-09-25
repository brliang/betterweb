"""The frontier in Postgres: enqueueing, seeds, planning and priority (PLAN.md §6.2)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl.frontier import (
    SEED,
    Candidate,
    Position,
    SeedError,
    add_seed,
    enqueue,
    ensure_urls,
    plan,
    recompute_priorities,
    release_stale_plans,
)
from app.db.web import (
    CrawlCycle,
    Document,
    Domain,
    DomainScore,
    FrontierEntry,
    GlobalScore,
    Link,
    Url,
)
from app.enums import DocumentType, FrontierReason
from app.settings import Settings

pytestmark = pytest.mark.anyio

SETTINGS = Settings(_env_file=None)
NOW = datetime(2026, 9, 23, 2, tzinfo=UTC)


async def entry(session: AsyncSession, url: str) -> FrontierEntry | None:
    url_id = await session.scalar(sa.select(Url.id).where(Url.url == url))
    return await session.get(FrontierEntry, url_id, populate_existing=True) if url_id else None


async def position(session: AsyncSession, url: str) -> Position | None:
    row = await entry(session, url)
    return Position(row.internal_depth, row.external_hops) if row else None


async def test_enqueue_inserts_with_the_cold_start_prior(session: AsyncSession) -> None:
    changes = await enqueue(
        session,
        [
            Candidate("https://pinned.example/a", Position(2, 0)),
            Candidate("https://other.example/b", Position(0, 1)),
        ],
        SETTINGS,
    )
    assert list(changes.values()) == [True, True]
    pinned = await entry(session, "https://pinned.example/a")
    other = await entry(session, "https://other.example/b")
    assert pinned is not None
    assert other is not None
    assert (pinned.priority, pinned.reason) == (1.0, FrontierReason.NEW)
    assert other.priority == 0.0


async def test_enqueue_keeps_the_minimum_of_each_field(session: AsyncSession) -> None:
    url = "https://example.com/x"
    await enqueue(session, [Candidate(url, Position(3, 1))], SETTINGS)
    # No improvement: nothing changes.
    assert await enqueue(session, [Candidate(url, Position(4, 1))], SETTINGS) == {}
    # A shorter path in one field improves only that field.
    changes = await enqueue(session, [Candidate(url, Position(5, 0))], SETTINGS)
    assert list(changes.values()) == [False]
    assert await position(session, url) == Position(3, 0)
    await enqueue(session, [Candidate(url, Position(1, 1))], SETTINGS)
    assert await position(session, url) == Position(1, 0)


async def test_an_improved_position_raises_the_priority(session: AsyncSession) -> None:
    url = "https://example.com/x"
    await enqueue(session, [Candidate(url, Position(0, 1))], SETTINGS)
    await enqueue(session, [Candidate(url, Position(2, 0))], SETTINGS)
    row = await entry(session, url)
    assert row is not None
    assert row.priority == 1.0


async def test_enqueue_skips_urls_beyond_the_limits(session: AsyncSession) -> None:
    far = Candidate("https://far.example/", Position(0, SETTINGS.max_external_hops + 1))
    deep = Candidate("https://example.com/deep", Position(SETTINGS.max_internal_depth + 1, 0))
    assert await enqueue(session, [far, deep], SETTINGS) == {}
    assert await entry(session, far.url) is None


async def test_a_dropped_url_is_never_enqueued_again(session: AsyncSession) -> None:
    url = "https://example.com/gone"
    url_id = (await ensure_urls(session, [url]))[url]
    await session.execute(sa.update(Url).where(Url.id == url_id).values(last_fetched_at=NOW))
    assert await enqueue(session, [Candidate(url, SEED)], SETTINGS) == {}


async def test_a_fetched_url_still_in_the_frontier_can_improve(session: AsyncSession) -> None:
    url = "https://example.com/page"
    await enqueue(session, [Candidate(url, Position(4, 0))], SETTINGS)
    await session.execute(sa.update(Url).where(Url.url == url).values(last_fetched_at=NOW))
    assert list((await enqueue(session, [Candidate(url, Position(1, 0))], SETTINGS)).values()) == [
        False
    ]


async def test_domain_scores_replace_the_cold_start_prior(session: AsyncSession) -> None:
    cycle = await make_cycle(session)
    scored, unscored = "https://scored.example/", "https://unscored.example/"
    ids = await ensure_urls(session, [scored, unscored])
    domain_id = await session.scalar(sa.select(Url.domain_id).where(Url.id == ids[scored]))
    session.add(DomainScore(domain_id=domain_id, cycle_id=cycle.id, score=0.25))
    await session.flush()
    await enqueue(session, [Candidate(scored, Position(0, 1)), Candidate(unscored, SEED)], SETTINGS)
    assert (await entry(session, scored)).priority == 0.25  # type: ignore[union-attr]
    # Once scores exist, a seed on an unscored domain gets no automatic head start.
    assert (await entry(session, unscored)).priority == 0.0  # type: ignore[union-attr]


async def test_add_seed(session: AsyncSession) -> None:
    feeds = ["https://Example.com/feed.xml?utm_source=x", "https://example.com/atom.xml"]
    url_id = await add_seed(session, "HTTPS://Example.com", SETTINGS, feeds)
    assert await add_seed(session, "https://example.com/", SETTINGS, feeds[:1]) == url_id
    assert await position(session, "https://example.com/") == SEED
    domain = await session.scalar(
        sa.select(Domain)
        .where(Domain.host == "example.com")
        .execution_options(populate_existing=True)
    )
    assert domain is not None
    assert domain.feed_urls == ["https://example.com/atom.xml", "https://example.com/feed.xml"]


@pytest.mark.parametrize(
    ("url", "feeds"), [("mailto:x@example.com", []), ("https://a.example/", ["ftp://x/"])]
)
async def test_add_seed_rejects_uncrawlable_urls(
    session: AsyncSession, url: str, feeds: list[str]
) -> None:
    with pytest.raises(SeedError):
        await add_seed(session, url, SETTINGS, feeds)


# Planning


async def make_cycle(session: AsyncSession, budget: int = 100) -> CrawlCycle:
    cycle = CrawlCycle(page_budget=budget, stats={})
    session.add(cycle)
    await session.flush()
    return cycle


async def add_entry(
    session: AsyncSession,
    url: str,
    *,
    priority: float = 0.0,
    reason: FrontierReason = FrontierReason.NEW,
    fetches: tuple[int, int] = (0, 0),
    due: bool = True,
) -> int:
    """An entry; `fetches` is (change_count, fetch_count)."""
    url_id = (await ensure_urls(session, [url]))[url]
    changes, count = fetches
    await session.execute(
        sa.update(Url).where(Url.id == url_id).values(change_count=changes, fetch_count=count)
    )
    session.add(
        FrontierEntry(
            url_id=url_id,
            internal_depth=0,
            external_hops=0,
            priority=priority,
            reason=reason,
            next_fetch_at=NOW - timedelta(hours=1) if due else NOW + timedelta(hours=1),
        )
    )
    await session.flush()
    return url_id


async def planned_urls(session: AsyncSession, cycle: CrawlCycle) -> set[str]:
    rows = await session.scalars(
        sa.select(Url.url)
        .join(FrontierEntry, FrontierEntry.url_id == Url.id)
        .where(FrontierEntry.cycle_id == cycle.id)
        .execution_options(populate_existing=True)
    )
    return {url.removeprefix("https://") for url in rows}


PLAN_SETTINGS = Settings(
    _env_file=None, cycle_recrawl_share=0.5, per_domain_min_delay_s=1, per_domain_concurrency=1
)


async def test_plan_splits_the_budget_and_caps_each_domain(session: AsyncSession) -> None:
    cycle = await make_cycle(session)
    for name, priority in [("a1", 0.9), ("a2", 0.8), ("a3", 0.7)]:
        await add_entry(session, f"https://a.example/{name}", priority=priority)
    await add_entry(session, "https://b.example/b1", priority=0.5)
    await add_entry(session, "https://b.example/b2", priority=0.4)
    recrawl = FrontierReason.RECRAWL
    await add_entry(session, "https://c.example/r1", reason=recrawl, fetches=(2, 2))
    await add_entry(session, "https://c.example/r2", reason=recrawl, fetches=(0, 3))
    await add_entry(session, "https://d.example/r3", reason=recrawl, fetches=(1, 2))
    await add_entry(session, "https://e.example/r4", reason=recrawl, fetches=(5, 5), due=False)

    # 5 slots: floor(2.5) = 2 re-crawls by change rate, then 3 new URLs by priority. A window
    # of 2.5 s at a 1 s delay caps each domain at 2 pages, so a3 loses out to b1.
    count = await plan(
        session, cycle_id=cycle.id, room=5, window_s=2.5, settings=PLAN_SETTINGS, now=NOW
    )
    assert count == 5
    assert await planned_urls(session, cycle) == {
        "c.example/r1",
        "d.example/r3",
        "a.example/a1",
        "a.example/a2",
        "b.example/b1",
    }


async def test_unused_recrawl_share_goes_to_new_urls(session: AsyncSession) -> None:
    cycle = await make_cycle(session)
    for index in range(4):
        await add_entry(session, f"https://a.example/{index}", priority=index)
    count = await plan(
        session, cycle_id=cycle.id, room=4, window_s=3600, settings=PLAN_SETTINGS, now=NOW
    )
    assert count == 4


async def test_plan_counts_already_planned_entries_per_domain(session: AsyncSession) -> None:
    cycle = await make_cycle(session)
    for index in range(4):
        await add_entry(session, f"https://a.example/{index}", priority=index)
    args = {"cycle_id": cycle.id, "window_s": 2.5, "settings": PLAN_SETTINGS, "now": NOW}
    assert await plan(session, room=1, **args) == 1  # type: ignore[arg-type]
    assert await plan(session, room=10, **args) == 1  # type: ignore[arg-type]
    assert await planned_urls(session, cycle) == {"a.example/3", "a.example/2"}


async def test_plan_skips_excluded_domains(session: AsyncSession) -> None:
    cycle = await make_cycle(session)
    await add_entry(session, "https://a.example/x")
    await add_entry(session, "https://b.example/x")
    domain_id = await session.scalar(sa.select(Domain.id).where(Domain.host == "a.example"))
    assert domain_id is not None
    await plan(
        session,
        cycle_id=cycle.id,
        room=10,
        window_s=3600,
        settings=PLAN_SETTINGS,
        now=NOW,
        exclude_domain_ids={domain_id},
    )
    assert await planned_urls(session, cycle) == {"b.example/x"}


async def test_plan_caps_each_domain_by_its_learned_delay(session: AsyncSession) -> None:
    cycle = await make_cycle(session)
    for host in ("quick", "slow", "new"):
        for index in range(10):
            await add_entry(session, f"https://{host}.example/{index}")
    for host, delay in (("quick", 0.5), ("slow", 2.5)):
        await session.execute(
            sa.update(Domain).where(Domain.host == f"{host}.example").values(learned_delay_s=delay)
        )
    options = Settings(_env_file=None, per_domain_min_delay_s=0.5, per_domain_start_delay_s=1)
    await plan(session, cycle_id=cycle.id, room=30, window_s=2.5, settings=options, now=NOW)

    planned = await planned_urls(session, cycle)
    hosts = [url.split(".", 1)[0] for url in planned]
    assert {host: hosts.count(host) for host in hosts} == {"quick": 5, "slow": 1, "new": 2}


async def test_release_stale_plans(session: AsyncSession) -> None:
    old, current = await make_cycle(session), await make_cycle(session)
    await add_entry(session, "https://a.example/old")
    await add_entry(session, "https://a.example/current")
    await session.execute(
        sa.update(FrontierEntry)
        .where(
            FrontierEntry.url_id
            == sa.select(Url.id).where(Url.url.endswith("/old")).scalar_subquery()
        )
        .values(cycle_id=old.id)
    )
    await plan(
        session, cycle_id=current.id, room=10, window_s=3600, settings=PLAN_SETTINGS, now=NOW
    )
    assert await release_stale_plans(session, current.id) == 1
    assert await planned_urls(session, old) == set()
    assert await planned_urls(session, current) == {"a.example/current"}


# Priority


async def add_document(session: AsyncSession, url: str, cycle: CrawlCycle, pagerank: float) -> int:
    url_id = (await ensure_urls(session, [url]))[url]
    domain_id = await session.scalar(sa.select(Url.domain_id).where(Url.id == url_id))
    document = Document(canonical_url_id=url_id, domain_id=domain_id, type=DocumentType.PAGE)
    session.add(document)
    await session.flush()
    session.add(GlobalScore(document_id=document.id, cycle_id=cycle.id, pagerank=pagerank))
    await session.flush()
    return document.id


async def link(session: AsyncSession, source: int, url: str) -> None:
    url_id = (await ensure_urls(session, [url]))[url]
    session.add(Link(src_document_id=source, dst_url_id=url_id, is_internal=False))
    await session.flush()


async def test_recompute_priorities_by_hand(session: AsyncSession) -> None:
    """Parents pass on PageRank / out-degree; the domain score is added with weight λ.

    P1 (PR 0.4) links to U1 and U2; P2 (PR 0.2) links to U1 only. x.example scores 0.1 and
    y.example 0.3. With λ = 2:
      U1 = 0.4/2 + 0.2/1 + 2 * 0.1 = 0.6
      U2 = 0.4/2         + 2 * 0.1 = 0.4
      U3 = (no parents)  + 2 * 0.3 = 0.6
    """
    cycle = await make_cycle(session)
    p1 = await add_document(session, "https://p.example/1", cycle, 0.4)
    p2 = await add_document(session, "https://p.example/2", cycle, 0.2)
    u1, u2, u3 = "https://x.example/1", "https://x.example/2", "https://y.example/3"
    for url in (u1, u2, u3):
        await add_entry(session, url)
    await link(session, p1, u1)
    await link(session, p1, u2)
    await link(session, p2, u1)
    for host, score in [("x.example", 0.1), ("y.example", 0.3)]:
        domain_id = await session.scalar(sa.select(Domain.id).where(Domain.host == host))
        session.add(DomainScore(domain_id=domain_id, cycle_id=cycle.id, score=score))
    await session.flush()

    assert await recompute_priorities(session, Settings(_env_file=None, domain_prior_weight=2)) == 3
    priorities = [(await entry(session, url)).priority for url in (u1, u2, u3)]  # type: ignore[union-attr]
    assert priorities == pytest.approx([0.6, 0.4, 0.6])


async def test_recompute_priorities_cold_start(session: AsyncSession) -> None:
    await enqueue(
        session,
        [
            Candidate("https://pinned.example/a", Position(3, 0)),
            Candidate("https://other.example/b", Position(0, 1)),
        ],
        SETTINGS,
    )
    await session.execute(sa.update(FrontierEntry).values(priority=0.5))
    await recompute_priorities(session, SETTINGS)
    assert (await entry(session, "https://pinned.example/a")).priority == 1.0  # type: ignore[union-attr]
    assert (await entry(session, "https://other.example/b")).priority == 0.0  # type: ignore[union-attr]
