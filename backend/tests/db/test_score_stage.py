"""The scoring stage end to end: the document graph and pins in; global scores, domain scores,
user PPR, frontier priorities and profile vectors out (PLAN.md §6.1 step 5, §6.5, milestone M6).
"""

import math
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import psycopg.errors
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl import frontier
from app.crawl.cycle import start_or_resume_cycle
from app.crawl.frontier import Candidate, Position
from app.db.base import Embedding
from app.db.usr import Feedback, Pin, User, UserInterest, UserPpr, UserProfileVector
from app.db.web import (
    CrawlCycle,
    Document,
    DocumentEmbedding,
    DomainScore,
    FrontierEntry,
    GlobalScore,
    Link,
    Topic,
    Url,
)
from app.enums import DocumentType, FeedbackKind, InterestSource, PinSource, ProfileVectorKind
from app.score.graph import load_graph
from app.score.pagerank import personalized_pagerank
from app.score.profiles import refresh_profile_vectors
from app.score.stage import run_score_stage
from app.settings import Settings
from tests.fakes import unit_vector

pytestmark = pytest.mark.anyio

MODEL = "fake-embedding"
# Damping 1/2 keeps the hand-computed scores simple fractions.
SETTINGS = Settings(_env_file=None, ppr_damping=0.5, ppr_tol=1e-12, embedding_model=MODEL)
NOW = datetime(2026, 9, 23, 3, tzinfo=UTC)


@pytest.fixture
async def cycle(session: AsyncSession) -> CrawlCycle:
    return await start_or_resume_cycle(session, SETTINGS)


async def url_id(session: AsyncSession, url: str) -> int:
    return (await frontier.ensure_urls(session, [url]))[url]


async def add_document(session: AsyncSession, url: str) -> int:
    canonical = await url_id(session, url)
    domain_id = await session.scalar(sa.select(Url.domain_id).where(Url.id == canonical))
    assert domain_id is not None
    document = Document(canonical_url_id=canonical, domain_id=domain_id, type=DocumentType.PAGE)
    session.add(document)
    await session.flush()
    await session.execute(sa.update(Url).where(Url.id == canonical).values(document_id=document.id))
    return document.id


async def link(session: AsyncSession, source: int, url: str) -> None:
    session.add(
        Link(src_document_id=source, dst_url_id=await url_id(session, url), is_internal=False)
    )
    await session.flush()


async def domain_id(session: AsyncSession, host: str) -> int:
    found = await session.scalar(sa.select(Url.domain_id).where(Url.url.like(f"https://{host}/%")))
    assert found is not None
    return found


async def add_user(session: AsyncSession, name: str, *pinned_hosts: str) -> uuid.UUID:
    user = User(email=f"{name}@example.com")
    session.add(user)
    await session.flush()
    for host in pinned_hosts:
        pinned = await domain_id(session, host)
        session.add(Pin(user_id=user.id, domain_id=pinned, source=PinSource.SURVEY))
    await session.flush()
    return user.id


A, B, C, E = (
    "https://one.example/a",
    "https://two.example/b",
    "https://two.example/c",
    "https://three.example/e",
)
UNCRAWLED = "https://four.example/new"


@pytest.fixture
async def diamond(session: AsyncSession) -> dict[str, int]:
    """a -> b, a -> c, b -> c, c -> a, c -> e, with e linking nowhere; plus links the graph
    drops: a link to itself, to a URL not crawled yet, and a second URL of the same document."""
    ids = {url: await add_document(session, url) for url in (A, B, C, E)}
    alias = await url_id(session, "https://two.example/c?page=1")
    await session.execute(sa.update(Url).where(Url.id == alias).values(document_id=ids[C]))
    for source, target in [(A, B), (A, C), (B, C), (C, A), (C, E), (A, A), (A, UNCRAWLED)]:
        await link(session, ids[source], target)
    await link(session, ids[B], "https://two.example/c?page=1")
    return {url.rsplit("/", 1)[1]: document_id for url, document_id in ids.items()}


# With a seeded alone and damping 1/2 (worked through in tests/test_pagerank.py):
HAND_COMPUTED = {"a": 32 / 55, "b": 8 / 55, "c": 12 / 55, "e": 3 / 55}


async def global_scores(session: AsyncSession) -> dict[int, float]:
    rows = await session.execute(sa.select(GlobalScore.document_id, GlobalScore.pagerank))
    return dict(rows.tuples().all())


async def user_ppr(session: AsyncSession, user_id: uuid.UUID) -> dict[int, float]:
    rows = await session.execute(
        sa.select(UserPpr.document_id, UserPpr.score).where(UserPpr.user_id == user_id)
    )
    return dict(rows.tuples().all())


async def test_the_graph_resolves_links_to_documents(
    session: AsyncSession, diamond: dict[str, int]
) -> None:
    graph = await load_graph(session)
    edges = {
        (int(graph.documents[s]), int(graph.documents[t]))
        for s, t in zip(graph.sources, graph.targets, strict=True)
    }
    d = diamond
    assert len(graph.sources) == 5
    assert edges == {
        (d["a"], d["b"]),
        (d["a"], d["c"]),
        (d["b"], d["c"]),
        (d["c"], d["a"]),
        (d["c"], d["e"]),
    }


async def test_scores_match_the_hand_computed_graph(
    session: AsyncSession, cycle: CrawlCycle, diamond: dict[str, int]
) -> None:
    user_id = await add_user(session, "reader", "one.example")
    await frontier.enqueue(session, [Candidate(UNCRAWLED, Position(0, 1))], SETTINGS)

    await run_score_stage(session, cycle, SETTINGS, NOW)

    expected = {diamond[name]: score for name, score in HAND_COMPUTED.items()}
    assert await user_ppr(session, user_id) == pytest.approx(expected)
    # One user, so the global surfer is theirs.
    assert await global_scores(session) == pytest.approx(expected)
    rows = await session.execute(sa.select(DomainScore.domain_id, DomainScore.score))
    assert dict(rows.tuples().all()) == pytest.approx(
        {
            await domain_id(session, "one.example"): 32 / 55,
            await domain_id(session, "two.example"): (8 / 55 + 12 / 55) / 2,
            await domain_id(session, "three.example"): 3 / 55,
        }
    )
    # a has four stored outlinks (itself and the uncrawled URL included): 32/55 / 4.
    priority = await session.scalar(
        sa.select(FrontierEntry.priority).where(
            FrontierEntry.url_id == await url_id(session, UNCRAWLED)
        )
    )
    assert priority == pytest.approx(8 / 55)
    stats = cycle.stats["scores"]
    assert isinstance(stats, dict)
    assert stats["pagerank"]["converged"] is True
    assert {k: stats["counts"][k] for k in ("documents", "links", "seed_domains", "user_ppr")} == {
        "documents": 4,
        "links": 5,
        "seed_domains": 1,
        "user_ppr": 4,
    }


async def test_global_scores_weight_each_user_equally(
    session: AsyncSession, cycle: CrawlCycle, diamond: dict[str, int]
) -> None:
    one = await add_user(session, "one", "one.example")
    # More pins don't give a user more weight.
    other = await add_user(session, "other", "two.example", "three.example")

    await run_score_stage(session, cycle, SETTINGS, NOW)

    # Half the seed weight is a's; the other user's half is split between their two domains,
    # and two.example's quarter between b and c.
    graph = await load_graph(session)
    seeds = {diamond["a"]: 1 / 2, diamond["b"]: 1 / 8, diamond["c"]: 1 / 8, diamond["e"]: 1 / 4}
    personalization = np.array([[seeds[int(d)]] for d in graph.documents])
    expected = personalized_pagerank(
        graph, personalization, damping=0.5, tol=1e-12, max_iter=1000
    ).scores[:, 0]
    assert await global_scores(session) == pytest.approx(
        dict(zip(graph.documents.tolist(), expected.tolist(), strict=True))
    )
    assert await user_ppr(session, one) != await user_ppr(session, other)


async def test_without_pins_global_scores_are_plain_pagerank(
    session: AsyncSession, cycle: CrawlCycle, diamond: dict[str, int]
) -> None:
    lurker = await add_user(session, "lurker")
    await run_score_stage(session, cycle, SETTINGS, NOW)

    scores = await global_scores(session)
    assert set(scores) == set(diamond.values())
    assert math.fsum(scores.values()) == pytest.approx(1.0)
    assert await user_ppr(session, lurker) == {}


async def test_user_ppr_keeps_the_top_k(
    session: AsyncSession, cycle: CrawlCycle, diamond: dict[str, int]
) -> None:
    user_id = await add_user(session, "reader", "one.example")
    settings = Settings(_env_file=None, ppr_damping=0.5, embedding_model=MODEL, user_ppr_top_k=2)
    await run_score_stage(session, cycle, settings, NOW)
    assert set(await user_ppr(session, user_id)) == {diamond["a"], diamond["c"]}


async def test_a_rerun_replaces_the_scores(
    session: AsyncSession, cycle: CrawlCycle, diamond: dict[str, int]
) -> None:
    user_id = await add_user(session, "reader", "one.example")
    await run_score_stage(session, cycle, SETTINGS, NOW)
    await session.execute(sa.delete(Pin))
    later = CrawlCycle(page_budget=1, stats={})
    session.add(later)
    await session.flush()

    await run_score_stage(session, later, SETTINGS, NOW)

    assert await user_ppr(session, user_id) == {}
    cycles = await session.scalars(sa.select(GlobalScore.cycle_id).distinct())
    assert list(cycles) == [later.id]
    assert math.fsum((await global_scores(session)).values()) == pytest.approx(1.0)


async def test_an_empty_graph(session: AsyncSession, cycle: CrawlCycle) -> None:
    await add_user(session, "early")
    counts = await run_score_stage(session, cycle, SETTINGS, NOW)
    assert counts["documents"] == 0
    assert await global_scores(session) == {}
    # No domain scores yet, so the frontier keeps its cold-start prior.
    assert await session.scalar(sa.select(sa.func.count()).select_from(DomainScore)) == 0


async def score_as_crawl_role(session: AsyncSession, cycle: CrawlCycle) -> None:
    # Rolling back the savepoint undoes the SET LOCAL too.
    async with session.begin_nested():
        await session.execute(sa.text("SET LOCAL ROLE discovery_crawl"))
        await run_score_stage(session, cycle, SETTINGS, NOW)


async def test_runs_with_the_scoring_role_only(
    session: AsyncSession, cycle: CrawlCycle, diamond: dict[str, int]
) -> None:
    """The stage needs nothing beyond discovery_score's grants (migration 0002), and it does
    need them: the crawl role can't read the pins."""
    await add_user(session, "reader", "one.example")
    await session.commit()
    with pytest.raises(ProgrammingError) as error:
        await score_as_crawl_role(session, cycle)
    assert isinstance(error.value.orig, psycopg.errors.InsufficientPrivilege)
    await session.execute(sa.text("SET LOCAL ROLE discovery_score"))
    counts = await run_score_stage(session, cycle, SETTINGS, NOW)
    assert counts["user_ppr"] == 4
    assert await session.scalar(sa.text("SELECT current_user")) == "discovery_score"


# Profile vectors


async def embed_document(session: AsyncSession, document_id: int, vector: Embedding) -> None:
    session.add(
        DocumentEmbedding(
            document_id=document_id,
            model=MODEL,
            vector=vector,
            input_hash="hash",
            document_updated_at=NOW,
        )
    )
    await session.flush()


async def add_interest(
    session: AsyncSession, user_id: uuid.UUID, name: str, weight: float, model: str = MODEL
) -> None:
    topic = Topic(
        external_id=name, name=name, tier=1, embedding=unit_vector(name), embedding_model=model
    )
    session.add(topic)
    await session.flush()
    session.add(
        UserInterest(
            user_id=user_id, topic_id=topic.id, weight=weight, source=InterestSource.SURVEY
        )
    )
    await session.flush()


async def feedback(
    session: AsyncSession, user_id: uuid.UUID, document_id: int, kind: FeedbackKind, at: datetime
) -> None:
    session.add(Feedback(user_id=user_id, document_id=document_id, kind=kind, created_at=at))
    await session.flush()


def normalized(*weighted: tuple[float, Embedding]) -> Embedding:
    total = [math.fsum(w * v[i] for w, v in weighted) for i in range(len(weighted[0][1]))]
    norm = math.sqrt(math.fsum(x * x for x in total))
    return [x / norm for x in total]


async def profile(session: AsyncSession, user_id: uuid.UUID) -> dict[ProfileVectorKind, Embedding]:
    rows = await session.execute(
        sa.select(UserProfileVector.kind, UserProfileVector.vector).where(
            UserProfileVector.user_id == user_id
        )
    )
    return {kind: list(vector) for kind, vector in rows}


async def test_profile_vectors(session: AsyncSession, diamond: dict[str, int]) -> None:
    user_id = await add_user(session, "reader")
    await add_interest(session, user_id, "Gardening", 1.0)
    await add_interest(session, user_id, "Cycling", 3.0)
    await add_interest(session, user_id, "Other model", 5.0, model="another-model")
    vectors = {name: unit_vector(name) for name in diamond}
    for name, document_id in diamond.items():
        if name != "e":  # e is not embedded yet
            await embed_document(session, document_id, vectors[name])
    half_life = timedelta(days=SETTINGS.liked_half_life_days)
    await feedback(session, user_id, diamond["a"], FeedbackKind.LIKE, NOW)
    await feedback(session, user_id, diamond["b"], FeedbackKind.LIKE, NOW - half_life)
    await feedback(session, user_id, diamond["e"], FeedbackKind.LIKE, NOW)
    # c was liked, then hidden: the latest decides.
    await feedback(session, user_id, diamond["c"], FeedbackKind.LIKE, NOW - timedelta(days=2))
    await feedback(session, user_id, diamond["c"], FeedbackKind.HIDE, NOW - timedelta(days=1))
    await feedback(session, user_id, diamond["a"], FeedbackKind.BLOCK_DOMAIN, NOW)

    kinds = await refresh_profile_vectors(session, user_id, SETTINGS, NOW)

    assert set(kinds) == set(ProfileVectorKind)
    stored = await profile(session, user_id)
    expected = {
        ProfileVectorKind.INTEREST: normalized(
            (1.0, unit_vector("Gardening")), (3.0, unit_vector("Cycling"))
        ),
        ProfileVectorKind.LIKED: normalized((1.0, vectors["a"]), (0.5, vectors["b"])),
        ProfileVectorKind.HIDDEN: vectors["c"],
    }
    for kind, vector in expected.items():
        assert stored[kind] == pytest.approx(vector, abs=1e-6)  # stored as float32


async def test_profile_vectors_without_signals_are_removed(
    session: AsyncSession, diamond: dict[str, int]
) -> None:
    user_id = await add_user(session, "reader")
    await embed_document(session, diamond["a"], unit_vector("a"))
    await feedback(session, user_id, diamond["a"], FeedbackKind.HIDE, NOW)
    assert await refresh_profile_vectors(session, user_id, SETTINGS, NOW) == [
        ProfileVectorKind.HIDDEN
    ]
    await session.execute(sa.delete(Feedback))
    assert await refresh_profile_vectors(session, user_id, SETTINGS, NOW) == []
    assert await profile(session, user_id) == {}


async def test_the_stage_refreshes_every_users_profile(
    session: AsyncSession, cycle: CrawlCycle, diamond: dict[str, int]
) -> None:
    user_id = await add_user(session, "reader")
    await add_interest(session, user_id, "Gardening", 1.0)
    counts = await run_score_stage(session, cycle, SETTINGS, NOW)
    assert counts["profile_vectors"] == 1
    assert set(await profile(session, user_id)) == {ProfileVectorKind.INTEREST}
