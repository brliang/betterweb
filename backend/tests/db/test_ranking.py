"""The feed and search against a small corpus (PLAN.md §6.6, §6.7, milestone M7): candidate
sources, hard filters, composition per page, pagination, persisted breakdowns, and the
evidence behind reasons."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.usr import Event, Feedback, Recommendation, UserInterest, UserSettings
from app.db.web import Document
from app.enums import (
    DocumentType,
    EventKind,
    FeedbackKind,
    ProfileVectorKind,
    RankingPreset,
    Slice,
    Surface,
)
from app.rank.context import hard_filters, load_context
from app.rank.explain import Breakdown
from app.rank.ranker import CursorError, Page, Ranker
from app.rank.scoring import Component
from app.settings import Settings
from tests.db.builders import (
    MODEL,
    add_document,
    add_interest,
    add_topic,
    add_user,
    direction,
    link,
    pin,
    set_ppr,
    set_vector,
)

pytestmark = pytest.mark.anyio

SETTINGS = Settings(
    _env_file=None, embedding_model=MODEL, feed_page_size=5, candidates_per_source=50
)
NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
INTEREST = direction((0, 1.0))
MAIN, SEMANTIC, GRAPH = Slice.MAIN, Slice.ADJACENT_SEMANTIC, Slice.ADJACENT_GRAPH


@dataclass
class World:
    user: uuid.UUID
    docs: dict[str, int]
    topics: dict[str, int]

    def name(self, document_id: int) -> str:
        return next(name for name, d in self.docs.items() if d == document_id)


@pytest.fixture
async def world(session: AsyncSession) -> World:
    """alice is interested in AI and pins mine.example, which links to friend.example, which
    links to far.example. other.example is unconnected.

    - mine/m1-m4: AI, near her interest, high PPR.
    - friend/f1: Travel (outside her interests), one domain hop from her pin.
    - friend/f2: AI, exactly her interest.
    - far/x1: Science, two hops.
    - other/o1: Hardware, a sibling of AI: adjacent. other/o2: a plain page (not wanted).
    """
    tech = await add_topic(session, "Technology")
    topics = {
        "tech": tech,
        "ai": await add_topic(session, "AI", tech),
        "hardware": await add_topic(session, "Hardware", tech),
        "travel": await add_topic(session, "Travel"),
        "science": await add_topic(session, "Science"),
    }
    user = await add_user(session, "alice@example.com")
    await add_interest(session, user, topics["ai"])
    await set_vector(session, user, ProfileVectorKind.INTEREST, INTEREST)
    await pin(session, user, "mine.example")

    ai = {topics["ai"]: 0.4}
    docs = {
        f"m{i}": await add_document(
            session,
            f"https://mine.example/m{i}",
            vector=direction((0, 1.0), (i, 0.3)),
            topics=ai,
            published_at=datetime(2026, 9, 20 + i, tzinfo=UTC),
        )
        for i in range(1, 5)
    }
    docs["f1"] = await add_document(
        session,
        "https://friend.example/f1",
        vector=direction((5, 1.0)),
        topics={topics["travel"]: 0.3},
        title="Night trains",
    )
    docs["f2"] = await add_document(
        session, "https://friend.example/f2", vector=INTEREST, topics=ai
    )
    docs["x1"] = await add_document(
        session,
        "https://far.example/x1",
        vector=direction((6, 1.0)),
        topics={topics["science"]: 0.3},
    )
    docs["o1"] = await add_document(
        session,
        "https://other.example/o1",
        vector=direction((7, 1.0), (0, 0.2)),
        topics={topics["hardware"]: 0.4},
    )
    docs["o2"] = await add_document(
        session, "https://other.example/o2", vector=INTEREST, kind=DocumentType.PAGE
    )
    await link(session, docs["m1"], "https://friend.example/f1")
    await link(session, docs["m2"], "https://friend.example/f2")
    await link(session, docs["f1"], "https://far.example/x1")
    await set_ppr(
        session,
        user,
        {docs["m1"]: 0.3, docs["m2"]: 0.2, docs["m3"]: 0.15, docs["m4"]: 0.1}
        | {docs["f1"]: 0.05, docs["f2"]: 0.05, docs["x1"]: 0.01},
    )
    session.add(
        UserSettings(
            user_id=user,
            preset=RankingPreset.BALANCED,
            weights={},
            exploration_pct=0.4,
            exploration_split={SEMANTIC.value: 0.5, GRAPH.value: 0.5},
            content_types=[DocumentType.ARTICLE.value],
        )
    )
    await session.flush()
    return World(user=user, docs=docs, topics=topics)


def ranker(session: AsyncSession, world: World, settings: Settings = SETTINGS) -> Ranker:
    return Ranker(session, world.user, settings, NOW)


def names(world: World, page: Page) -> list[str]:
    return [world.name(item.document.id) for item in page.items]


async def test_a_page_mixes_main_and_both_exploration_slices(
    session: AsyncSession, world: World
) -> None:
    page = await ranker(session, world).feed(None)

    # Page of 5 at 40% exploration: 3 main, 1 semantic, 1 graph, spread to positions 1 and 3.
    assert [item.slice for item in page.items] == [MAIN, SEMANTIC, MAIN, GRAPH, MAIN]
    served = names(world, page)
    assert served[1] == "o1"  # tagged Hardware, next to her interest in AI
    assert served[3] == "f1"  # Travel, from the site her pin links to
    # At most two from mine.example; friend/f2 (exactly her interest) takes the third main slot.
    assert served[0] == "m1"
    assert sum(name.startswith("m") for name in served) == 2
    assert "f2" in served
    assert [item.position for item in page.items] == [0, 1, 2, 3, 4]


async def test_every_page_is_persisted_with_its_breakdown(
    session: AsyncSession, world: World
) -> None:
    page = await ranker(session, world).feed(None)
    rows = {
        row.id: row
        for row in await session.scalars(
            sa.select(Recommendation).where(Recommendation.user_id == world.user)
        )
    }
    assert sorted(rows) == sorted(item.recommendation_id for item in page.items)
    for item in page.items:
        row = rows[item.recommendation_id]
        assert (row.document_id, row.slice, row.surface) == (
            item.document.id,
            item.slice,
            Surface.FEED,
        )
        breakdown = Breakdown.model_validate(row.components)
        assert sum(c.contribution for c in breakdown.components) == pytest.approx(row.score)
        assert {c.name for c in breakdown.components} == {
            Component.INTEREST,
            Component.PPR,
            Component.FEEDBACK,
            Component.RECENCY,
            Component.HIDE,
        }
        assert item.reasons, f"no reasons for {world.name(item.document.id)}"
        assert all(0 <= c.value <= 1 for c in breakdown.components)


async def test_evidence_names_the_trusted_linkers_and_the_exploration_route(
    session: AsyncSession, world: World
) -> None:
    await ranker(session, world).feed(None)
    page2 = await ranker(session, world).feed(await first_cursor(session, world))
    breakdowns = {
        world.name(row.document_id): Breakdown.model_validate(row.components)
        for row in await session.scalars(sa.select(Recommendation))
    }
    f1 = breakdowns["f1"].evidence
    assert (f1.trusted_linkers, f1.trusted_linker_count) == (["mine.example"], 1)
    assert f1.exploration is not None
    assert f1.exploration.path == ["mine.example"]
    assert f1.exploration.outside_topic is not None
    assert f1.exploration.outside_topic.name == "Travel"

    o1 = breakdowns["o1"].evidence.exploration
    assert o1 is not None
    assert o1.adjacent_topic is not None
    assert o1.interest_topic is not None
    assert (o1.adjacent_topic.name, o1.interest_topic.name) == ("Hardware", "AI")

    m1 = breakdowns["m1"].evidence
    assert m1.pinned_domain == "mine.example"
    assert [t.name for t in m1.interest_topics] == ["AI"]

    # Page 2 reaches far.example, two hops out, as its graph item.
    x1 = next(item for item in page2.items if world.name(item.document.id) == "x1")
    assert x1.slice == GRAPH
    x1_evidence = breakdowns["x1"].evidence
    assert x1_evidence.exploration is not None
    assert x1_evidence.exploration.path == ["mine.example", "friend.example"]
    assert x1_evidence.other_linkers == ["friend.example"]
    assert x1.reasons[0].text == "Exploring: Science, 2 links away from mine.example"


async def first_cursor(session: AsyncSession, world: World) -> str:
    first = await session.scalar(
        sa.select(sa.func.min(Recommendation.id)).where(Recommendation.user_id == world.user)
    )
    return str(first)


async def test_pages_never_repeat_and_end(session: AsyncSession, world: World) -> None:
    served: list[str] = []
    page = await ranker(session, world).feed(None)
    pages = 1
    while True:
        served += names(world, page)
        assert [item.position for item in page.items] == list(
            range(len(served) - len(page.items), len(served))
        )
        if page.next_cursor is None:
            break
        page = await ranker(session, world).feed(page.next_cursor)
        pages += 1
    assert len(served) == len(set(served))
    # Everything but the plain page o2, which isn't a wanted content type.
    assert sorted(served) == ["f1", "f2", "m1", "m2", "m3", "m4", "o1", "x1"]
    assert pages == 2


async def test_a_new_feed_session_starts_over(session: AsyncSession, world: World) -> None:
    first = await ranker(session, world).feed(None)
    again = await ranker(session, world).feed(None)
    assert names(world, first) == names(world, again)
    assert first.next_cursor != again.next_cursor


async def test_cursors_belong_to_one_user_and_surface(session: AsyncSession, world: World) -> None:
    page = await ranker(session, world).feed(None)
    assert page.next_cursor is not None
    with pytest.raises(CursorError):
        await ranker(session, world).feed("not-a-number")
    with pytest.raises(CursorError):
        await ranker(session, world).search("trains", INTEREST, page.next_cursor)
    stranger = await add_user(session, "mallory@example.com")
    with pytest.raises(CursorError):
        await Ranker(session, stranger, SETTINGS, NOW).feed(page.next_cursor)


async def feed_names(session: AsyncSession, world: World) -> set[str]:
    settings = SETTINGS.model_copy(update={"feed_page_size": 50})
    return set(names(world, await ranker(session, world, settings).feed(None)))


async def test_hard_filters(session: AsyncSession, world: World) -> None:
    docs = world.docs
    session.add_all(
        [
            Feedback(user_id=world.user, document_id=docs["m1"], kind=FeedbackKind.HIDE),
            Feedback(user_id=world.user, document_id=docs["m2"], kind=FeedbackKind.LIKE),
            # Blocks all of far.example.
            Feedback(user_id=world.user, document_id=docs["x1"], kind=FeedbackKind.BLOCK_DOMAIN),
            Event(
                user_id=world.user,
                document_id=docs["m3"],
                kind=EventKind.CLICK,
                surface=Surface.FEED,
            ),
        ]
    )
    # f2 shown on four different recommendations without a click (more than 3): filtered.
    # f1 shown three times, and one impression logged twice for the same recommendation.
    for name, shown in (("f2", 4), ("f1", 3)):
        for _ in range(shown):
            recommendation = Recommendation(
                user_id=world.user,
                document_id=docs[name],
                surface=Surface.FEED,
                slice=MAIN,
                score=0,
                components={},
            )
            session.add(recommendation)
            await session.flush()
            session.add(
                Event(
                    user_id=world.user,
                    document_id=docs[name],
                    recommendation_id=recommendation.id,
                    kind=EventKind.IMPRESSION,
                    surface=Surface.FEED,
                )
            )
        session.add(
            Event(
                user_id=world.user,
                document_id=docs[name],
                recommendation_id=recommendation.id,
                kind=EventKind.IMPRESSION,
                surface=Surface.FEED,
            )
        )
    await session.flush()

    assert await feed_names(session, world) == {"m4", "f1", "o1"}

    # Search keeps what was liked, clicked or shown before; hidden and blocked stay out.
    context = await load_context(session, world.user, SETTINGS)
    searchable = set(
        await session.scalars(
            sa.select(Document.id).where(*hard_filters(context, SETTINGS, seen=False))
        )
    )
    assert {world.name(d) for d in searchable} == {"m2", "m3", "m4", "f1", "f2", "o1"}


async def test_content_types_come_from_the_settings(session: AsyncSession, world: World) -> None:
    await session.execute(
        sa.update(UserSettings)
        .where(UserSettings.user_id == world.user)
        .values(content_types=[DocumentType.PAGE.value])
    )
    assert await feed_names(session, world) == {"o2"}


async def test_before_the_survey_the_defaults_apply(session: AsyncSession, world: World) -> None:
    await session.execute(sa.delete(UserSettings).where(UserSettings.user_id == world.user))
    page = await ranker(session, world).feed(None)
    # Default 20% of a 5-item page is one exploration slot, the semantic one, in the middle.
    assert [item.slice for item in page.items] == [MAIN, MAIN, SEMANTIC, MAIN, MAIN]
    assert "o2" not in names(world, page)  # plain pages are opt-in


async def test_the_semantic_band_skips_the_top_matches(session: AsyncSession, world: World) -> None:
    # Interested in all of Technology: a tier-1 interest has no adjacent topics, so semantic
    # exploration comes from the similarity band alone. By similarity to her interest the
    # eligible documents go f2, m1-m4, o1, then f1 and x1: past the 6 nearest, only those two.
    await session.execute(sa.delete(UserInterest).where(UserInterest.user_id == world.user))
    await add_interest(session, world.user, world.topics["tech"])
    settings = SETTINGS.model_copy(update={"explore_band_skip": 6, "explore_band_size": 10})
    page = await ranker(session, world, settings).feed(None)
    [semantic] = [item for item in page.items if item.slice == SEMANTIC]
    assert world.name(semantic.document.id) in {"f1", "x1"}
    assert semantic.reasons[0].text == "Exploring: near your interests, past the closest matches"


async def test_search_ranks_by_the_query_and_keeps_liked_documents(
    session: AsyncSession, world: World
) -> None:
    query = direction((5, 1.0))  # f1's direction
    session.add(Feedback(user_id=world.user, document_id=world.docs["f1"], kind=FeedbackKind.LIKE))
    await session.flush()
    page = await ranker(session, world).search("night trains", query, None)
    assert names(world, page)[0] == "f1"
    assert all(item.slice == MAIN for item in page.items)
    row = await session.get(Recommendation, page.items[0].recommendation_id)
    assert row is not None
    assert (row.surface, row.query) == (Surface.SEARCH, "night trains")
    breakdown = Breakdown.model_validate(row.components)
    query_component = next(c for c in breakdown.components if c.name == Component.QUERY)
    assert query_component.value == 1.0
    assert query_component.weight == SETTINGS.search_query_weight
    assert page.items[0].reasons[0].text == "Matches your search"

    # The next page continues this search, not another.
    assert page.next_cursor is not None
    more = await ranker(session, world).search("night trains", query, page.next_cursor)
    assert not set(names(world, page)) & set(names(world, more))
    with pytest.raises(CursorError):
        await ranker(session, world).search("other", query, page.next_cursor)


async def test_likes_shape_the_ranking_and_reasons(session: AsyncSession, world: World) -> None:
    session.add(Feedback(user_id=world.user, document_id=world.docs["f1"], kind=FeedbackKind.LIKE))
    await set_vector(session, world.user, ProfileVectorKind.LIKED, direction((6, 1.0)))
    await session.flush()
    settings = SETTINGS.model_copy(update={"feed_page_size": 50})
    page = await ranker(session, world, settings).feed(None)
    x1 = next(item for item in page.items if world.name(item.document.id) == "x1")
    row = await session.get(Recommendation, x1.recommendation_id)
    assert row is not None
    evidence = Breakdown.model_validate(row.components).evidence
    assert evidence.nearest_liked is not None
    assert evidence.nearest_liked.title == "Night trains"
