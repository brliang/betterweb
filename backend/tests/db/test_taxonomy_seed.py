"""Seeding and embedding web.topics (PLAN.md §6.4)."""

import math

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import EMBEDDING_DIMENSIONS
from app.db.web import Topic
from app.taxonomy import Description, TaxonomyError, TopicSpec, embed_topics, seed_topics
from tests.fakes import FakeEmbeddings

pytestmark = pytest.mark.anyio

INSTRUCTION = "Find pages"

TOPICS = [
    TopicSpec("1", "Tech", None, 1),
    TopicSpec("x-history", "History", None, 1),
    TopicSpec("2", "Computing", "1", 2),
    TopicSpec("3", "Programming", "1", 2),
]


def describe(topics: list[TopicSpec]) -> dict[str, Description]:
    return {
        topic.external_id: Description(name=topic.name, description=f"About {topic.name}.")
        for topic in topics
    }


async def rows(session: AsyncSession) -> dict[str, Topic]:
    return {row.external_id: row for row in await session.scalars(sa.select(Topic))}


async def test_seed_inserts_the_tree(session: AsyncSession) -> None:
    stats = await seed_topics(session, TOPICS, describe(TOPICS))

    assert (stats.inserted, stats.updated, stats.deleted) == (4, 0, 0)
    topics = await rows(session)
    assert topics["2"].parent_id == topics["1"].id
    assert topics["2"].tier == 2
    assert topics["1"].parent_id is None
    assert topics["3"].description == "About Programming."


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    await seed_topics(session, TOPICS, describe(TOPICS))
    stats = await seed_topics(session, TOPICS, describe(TOPICS))
    assert (stats.inserted, stats.updated, stats.deleted) == (0, 0, 0)


async def test_reseed_updates_deletes_and_resets_changed_embeddings(
    session: AsyncSession,
) -> None:
    await seed_topics(session, TOPICS, describe(TOPICS))
    await embed_topics(session, FakeEmbeddings(), INSTRUCTION)

    renamed = [TOPICS[0], TOPICS[1], TopicSpec("3", "Software Development", "1", 2)]
    stats = await seed_topics(session, renamed, describe(renamed))

    assert (stats.inserted, stats.updated, stats.deleted) == (0, 1, 1)
    topics = await rows(session)
    assert topics.keys() == {"1", "x-history", "3"}
    assert topics["3"].name == "Software Development"
    assert topics["3"].embedding is None
    assert topics["1"].embedding is not None


async def test_seed_deletes_children_before_parents(session: AsyncSession) -> None:
    await seed_topics(session, TOPICS, describe(TOPICS))
    remaining = [TOPICS[1]]
    stats = await seed_topics(session, remaining, describe(remaining))
    assert stats.deleted == 3
    assert (await rows(session)).keys() == {"x-history"}


async def test_seed_needs_current_descriptions(session: AsyncSession) -> None:
    descriptions = describe(TOPICS)
    descriptions["3"] = Description(name="Old name", description="Stale.")
    with pytest.raises(TaxonomyError, match="1 topics lack a current description"):
        await seed_topics(session, TOPICS, descriptions)


async def test_embed_topics_embeds_path_and_description_once(session: AsyncSession) -> None:
    await seed_topics(session, TOPICS, describe(TOPICS))
    provider = FakeEmbeddings()

    assert await embed_topics(session, provider, INSTRUCTION) == 4
    assert await embed_topics(session, provider, INSTRUCTION) == 0

    # As queries (the fake joins instruction and text), to match plain document embeddings.
    assert sorted(provider.calls[0]) == [
        "Find pages: History: About History.",
        "Find pages: Tech > Computing: About Computing.",
        "Find pages: Tech > Programming: About Programming.",
        "Find pages: Tech: About Tech.",
    ]
    topic = (await rows(session))["2"]
    assert topic.embedding_model == "fake-embedding"
    assert topic.embedding is not None
    assert len(topic.embedding) == EMBEDDING_DIMENSIONS
    assert math.isclose(sum(x * x for x in topic.embedding), 1, rel_tol=1e-4)


async def test_embed_topics_redoes_topics_from_another_model(session: AsyncSession) -> None:
    await seed_topics(session, TOPICS, describe(TOPICS))
    await embed_topics(session, FakeEmbeddings("old-model"), INSTRUCTION)
    assert await embed_topics(session, FakeEmbeddings("new-model"), INSTRUCTION) == 4


async def test_embed_topics_can_redo_everything(session: AsyncSession) -> None:
    await seed_topics(session, TOPICS, describe(TOPICS))
    provider = FakeEmbeddings()
    await embed_topics(session, provider, INSTRUCTION)
    assert await embed_topics(session, provider, "A new instruction", redo_all=True) == 4
