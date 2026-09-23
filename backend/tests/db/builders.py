"""Building small corpora for ranking and API tests: documents with chosen embeddings, topic
tags, links, users and their signals."""

import math
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawl import frontier
from app.db.base import EMBEDDING_DIMENSIONS, Embedding
from app.db.usr import Pin, User, UserInterest, UserPpr, UserProfileVector
from app.db.web import (
    CrawlCycle,
    Document,
    DocumentEmbedding,
    DocumentTopic,
    Link,
    Topic,
    Url,
)
from app.enums import DocumentType, InterestSource, PinSource, ProfileVectorKind

MODEL = "fake-embedding"
EPOCH = datetime(2026, 9, 1, tzinfo=UTC)


def direction(*weights: tuple[int, float]) -> Embedding:
    """A unit vector with the given weights on the given axes, e.g. direction((0, 1), (1, 1))."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for axis, weight in weights:
        vector[axis] = weight
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


async def add_topic(
    session: AsyncSession, name: str, parent: int | None = None, external_id: str | None = None
) -> int:
    tier = 1
    if parent is not None:
        parent_tier = await session.scalar(sa.select(Topic.tier).where(Topic.id == parent))
        tier = (parent_tier or 0) + 1
    topic = Topic(external_id=external_id or f"x-{name}", name=name, parent_id=parent, tier=tier)
    session.add(topic)
    await session.flush()
    return topic.id


async def add_document(
    session: AsyncSession,
    url: str,
    *,
    vector: Embedding | None = None,
    topics: dict[int, float] | None = None,
    kind: DocumentType = DocumentType.ARTICLE,
    title: str | None = None,
    published_at: datetime | None = EPOCH,
) -> int:
    url_id = (await frontier.ensure_urls(session, [url]))[url]
    domain_id = await session.scalar(sa.select(Url.domain_id).where(Url.id == url_id))
    assert domain_id is not None
    document = Document(
        canonical_url_id=url_id,
        domain_id=domain_id,
        type=kind,
        title=title or url.rsplit("/", 1)[-1],
        excerpt=f"About {url}",
        published_at=published_at,
    )
    session.add(document)
    await session.flush()
    await session.execute(sa.update(Url).where(Url.id == url_id).values(document_id=document.id))
    if vector is not None:
        session.add(
            DocumentEmbedding(
                document_id=document.id,
                model=MODEL,
                vector=vector,
                input_hash="x",
                document_updated_at=EPOCH,
            )
        )
    for topic_id, score in (topics or {}).items():
        session.add(DocumentTopic(document_id=document.id, topic_id=topic_id, score=score))
    await session.flush()
    return document.id


async def link(session: AsyncSession, source: int, url: str) -> None:
    url_id = (await frontier.ensure_urls(session, [url]))[url]
    session.add(Link(src_document_id=source, dst_url_id=url_id, is_internal=False))
    await session.flush()


async def domain_of(session: AsyncSession, host: str) -> int:
    domains = await frontier.ensure_domains(session, [host])
    return domains[host]


async def add_user(session: AsyncSession, email: str) -> uuid.UUID:
    user = User(email=email)
    session.add(user)
    await session.flush()
    return user.id


async def pin(session: AsyncSession, user_id: uuid.UUID, host: str) -> int:
    domain_id = await domain_of(session, host)
    session.add(Pin(user_id=user_id, domain_id=domain_id, source=PinSource.SURVEY))
    await session.flush()
    return domain_id


async def add_interest(
    session: AsyncSession, user_id: uuid.UUID, topic_id: int, weight: float = 1.0
) -> None:
    session.add(
        UserInterest(
            user_id=user_id, topic_id=topic_id, weight=weight, source=InterestSource.SURVEY
        )
    )
    await session.flush()


async def set_vector(
    session: AsyncSession, user_id: uuid.UUID, kind: ProfileVectorKind, vector: Embedding
) -> None:
    session.add(UserProfileVector(user_id=user_id, kind=kind, vector=vector))
    await session.flush()


async def set_ppr(session: AsyncSession, user_id: uuid.UUID, scores: dict[int, float]) -> int:
    cycle = CrawlCycle(page_budget=1)
    session.add(cycle)
    await session.flush()
    session.add_all(
        UserPpr(user_id=user_id, document_id=document_id, score=score, cycle_id=cycle.id)
        for document_id, score in scores.items()
    )
    await session.flush()
    return cycle.id
