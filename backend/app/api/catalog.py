"""What the survey offers: the topic taxonomy and the suggested sources (PLAN.md §7)."""

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import DbSession
from app.db.web import Topic
from app.preferences import suggested_sources
from app.rank.explain import TopicRef

router = APIRouter(tags=["catalog"])


class TopicOut(BaseModel):
    id: int
    external_id: str
    name: str
    parent_id: int | None
    tier: int
    description: str | None


class SuggestedSourceOut(BaseModel):
    name: str
    url: str
    host: str
    feed: str
    description: str
    topics: list[TopicRef]


@router.get("/taxonomy")
async def taxonomy(session: DbSession) -> list[TopicOut]:
    """Every topic, tier by tier; the survey offers tier 1, expandable to tier 2."""
    topics = await session.scalars(sa.select(Topic).order_by(Topic.tier, Topic.name, Topic.id))
    return [
        TopicOut(
            id=topic.id,
            external_id=topic.external_id,
            name=topic.name,
            parent_id=topic.parent_id,
            tier=topic.tier,
            description=topic.description,
        )
        for topic in topics
    ]


@router.get("/suggested-sources")
async def list_suggested_sources(session: DbSession) -> list[SuggestedSourceOut]:
    sources = suggested_sources()
    external_ids = {external_id for source in sources for external_id in source.topics}
    topics = {
        external_id: TopicRef(id=topic_id, name=name)
        for external_id, topic_id, name in await session.execute(
            sa.select(Topic.external_id, Topic.id, Topic.name).where(
                Topic.external_id.in_(external_ids)
            )
        )
    }
    return [
        SuggestedSourceOut(
            name=source.name,
            url=str(source.url),
            host=source.host,
            feed=str(source.feed),
            description=source.description,
            topics=[topics[t] for t in source.topics if t in topics],
        )
        for source in sources
    ]
