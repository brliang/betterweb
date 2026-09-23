"""Topic tagging (PLAN.md §6.4): a document's topics are the TAG_MAX_TOPICS topics most similar
to its embedding, at or above TAG_MIN_SIMILARITY and within TAG_MAX_GAP of its best topic.
Zero-shot: topic embeddings (instructed queries) against document embeddings (plain passages),
both from the same model.

Runs in Postgres: an exact scan over the few hundred topic vectors per document.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import DocumentEmbedding, DocumentTopic, Topic
from app.settings import Settings


async def embedded_topic_count(session: AsyncSession, model: str) -> int:
    count = await session.scalar(
        sa.select(sa.func.count()).select_from(Topic).where(Topic.embedding_model == model)
    )
    return count or 0


async def tag_documents(
    session: AsyncSession,
    model: str,
    settings: Settings,
    document_ids: Sequence[int] | None = None,
) -> int:
    """Replace the topics of `document_ids` (every embedded document when None) from their
    `model` embeddings. Returns the number of tags written. The caller commits."""
    embeddings = DocumentEmbedding.__table__
    distance = Topic.embedding.cosine_distance(embeddings.c.vector)
    nearest = (
        sa.select(Topic.id.label("topic_id"), (1 - distance).label("score"))
        .where(Topic.embedding_model == model)
        .order_by(distance)
        .limit(settings.tag_max_topics)
        .lateral("nearest")
    )
    ranked = (
        sa.select(
            embeddings.c.document_id,
            nearest.c.topic_id,
            nearest.c.score,
            sa.func.max(nearest.c.score).over(partition_by=embeddings.c.document_id).label("best"),
        )
        .select_from(embeddings.join(nearest, sa.true()))
        .where(embeddings.c.model == model)
    )
    clear = sa.delete(DocumentTopic)
    if document_ids is not None:
        ranked = ranked.where(embeddings.c.document_id.in_(document_ids))
        clear = clear.where(DocumentTopic.document_id.in_(document_ids))
    candidates = ranked.subquery("ranked")
    tags = sa.select(candidates.c.document_id, candidates.c.topic_id, candidates.c.score).where(
        candidates.c.score >= settings.tag_min_similarity,
        candidates.c.score >= candidates.c.best - settings.tag_max_gap,
    )
    await session.execute(clear)
    inserted = (
        sa.insert(DocumentTopic)
        .from_select(["document_id", "topic_id", "score"], tags)
        .returning(DocumentTopic.topic_id)
        .cte("inserted")
    )
    return await session.scalar(sa.select(sa.func.count()).select_from(inserted)) or 0
