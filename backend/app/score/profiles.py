"""User profile vectors (PLAN.md §6.5), in the embedding space of the configured model:

- `interest`: the mean of the user's interest topics' embeddings, weighted by interest weight.
- `liked`: the mean of liked documents' embeddings, each like halving in weight every
  LIKED_HALF_LIFE_DAYS.
- `hidden`: the mean of hidden documents' embeddings.

Each vector is stored L2-normalized (the direction is all cosine similarity sees). A document
counts by the user's latest like or hide of it, so hiding a liked document moves it to
`hidden`. A kind with nothing to average (no interests, likes or hides yet, or none embedded)
has no row.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import numpy as np
import numpy.typing as npt
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Embedding
from app.db.usr import Feedback, UserInterest, UserProfileVector
from app.db.web import DocumentEmbedding, Topic
from app.enums import FeedbackKind, ProfileVectorKind
from app.settings import Settings

SECONDS_PER_DAY = 86_400


def weighted_mean(vectors: Sequence[Embedding], weights: Sequence[float]) -> Embedding | None:
    """The L2-normalized weighted mean, or None if there is nothing (or nothing but zero)."""
    if not vectors:
        return None
    matrix: npt.NDArray[np.float64] = np.array(vectors, dtype=np.float64)
    mean = np.average(matrix, axis=0, weights=np.array(weights, dtype=np.float64))
    norm = float(np.linalg.norm(mean))
    if norm == 0:
        return None
    return [float(value) for value in mean / norm]


def like_weight(liked_at: datetime, now: datetime, half_life_days: float) -> float:
    age_days = max(0.0, (now - liked_at).total_seconds() / SECONDS_PER_DAY)
    return float(0.5 ** (age_days / half_life_days))


async def _interest(session: AsyncSession, user_id: uuid.UUID, model: str) -> Embedding | None:
    rows = (
        await session.execute(
            sa.select(Topic.embedding, UserInterest.weight)
            .join(UserInterest, UserInterest.topic_id == Topic.id)
            .where(
                UserInterest.user_id == user_id,
                Topic.embedding_model == model,
                Topic.embedding.is_not(None),
                UserInterest.weight > 0,
            )
        )
    ).all()
    return weighted_mean([row[0] for row in rows if row[0] is not None], [row[1] for row in rows])


async def _feedback(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings, now: datetime
) -> dict[ProfileVectorKind, Embedding | None]:
    latest = (
        sa.select(Feedback.document_id, Feedback.kind, Feedback.created_at)
        .distinct(Feedback.document_id)
        .where(
            Feedback.user_id == user_id,
            Feedback.kind.in_([FeedbackKind.LIKE, FeedbackKind.HIDE]),
        )
        .order_by(Feedback.document_id, Feedback.created_at.desc(), Feedback.id.desc())
        .subquery()
    )
    rows = (
        await session.execute(
            sa.select(latest.c.kind, latest.c.created_at, DocumentEmbedding.vector).join(
                DocumentEmbedding,
                sa.and_(
                    DocumentEmbedding.document_id == latest.c.document_id,
                    DocumentEmbedding.model == settings.embedding_model,
                ),
            )
        )
    ).all()
    liked = [(vector, created_at) for kind, created_at, vector in rows if kind == FeedbackKind.LIKE]
    hidden = [vector for kind, _, vector in rows if kind == FeedbackKind.HIDE]
    return {
        ProfileVectorKind.LIKED: weighted_mean(
            [vector for vector, _ in liked],
            [like_weight(at, now, settings.liked_half_life_days) for _, at in liked],
        ),
        ProfileVectorKind.HIDDEN: weighted_mean(hidden, [1.0] * len(hidden)),
    }


async def refresh_profile_vectors(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings, now: datetime
) -> list[ProfileVectorKind]:
    """Recompute and store the user's profile vectors; returns the kinds that have one.

    The caller commits."""
    vectors = {
        ProfileVectorKind.INTEREST: await _interest(session, user_id, settings.embedding_model),
        **await _feedback(session, user_id, settings, now),
    }
    stored = [kind for kind, vector in vectors.items() if vector is not None]
    if stored:
        statement = pg_insert(UserProfileVector).values(
            [
                {"user_id": user_id, "kind": kind, "vector": vector, "updated_at": now}
                for kind, vector in vectors.items()
                if vector is not None
            ]
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[UserProfileVector.user_id, UserProfileVector.kind],
                set_={"vector": statement.excluded.vector, "updated_at": now},
            )
        )
    await session.execute(
        sa.delete(UserProfileVector).where(
            UserProfileVector.user_id == user_id, UserProfileVector.kind.not_in(stored)
        )
    )
    return stored
