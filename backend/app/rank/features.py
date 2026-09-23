"""Raw ranking signals for a candidate set, and the evidence behind served items.

Vector math runs in Postgres (pgvector), so only scalars leave the database: cosines to the
profile vectors and the query, and the highest similarity to any recently hidden document.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.base import Embedding
from app.db.usr import UserPpr
from app.db.web import Document, DocumentEmbedding, DocumentTopic, Domain, Link, Url
from app.enums import DocumentType, ProfileVectorKind
from app.rank.context import UserContext
from app.rank.explain import LikedRef
from app.rank.scoring import Signals
from app.settings import Settings

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class CandidateSet:
    ids: list[int]
    domain_ids: list[int]
    types: list[DocumentType]
    published: list[bool]
    tags: list[list[tuple[int, float]]]
    """(topic ID, score) per candidate."""
    signals: Signals

    def __len__(self) -> int:
        return len(self.ids)


def _cosine(vector: Embedding | None) -> sa.ColumnElement[float]:
    if vector is None:
        return sa.literal(0.0, sa.Float)
    return sa.func.coalesce(1 - DocumentEmbedding.vector.cosine_distance(vector), 0.0)


async def load_candidates(
    session: AsyncSession,
    ids: Sequence[int],
    context: UserContext,
    settings: Settings,
    now: datetime,
    query: Embedding | None = None,
) -> CandidateSet:
    """Signals for the documents `ids`, in that order."""
    model = settings.embedding_model
    vectors = context.vectors
    hidden = aliased(DocumentEmbedding)
    hidden_max = (
        sa.select(sa.func.max(1 - hidden.vector.cosine_distance(DocumentEmbedding.vector)))
        .where(hidden.model == model, hidden.document_id.in_(context.hidden))
        .scalar_subquery()
    )
    rows = {
        row.id: row
        for row in await session.execute(
            sa.select(
                Document.id,
                Document.domain_id,
                Document.type,
                Document.published_at,
                Document.created_at,
                _cosine(vectors.get(ProfileVectorKind.INTEREST)).label("interest"),
                _cosine(vectors.get(ProfileVectorKind.LIKED)).label("liked"),
                _cosine(vectors.get(ProfileVectorKind.HIDDEN)).label("hidden"),
                _cosine(query).label("query"),
                (sa.func.coalesce(hidden_max, 0.0) if context.hidden else sa.literal(0.0)).label(
                    "hidden_max"
                ),
                sa.func.coalesce(UserPpr.score, 0.0).label("ppr"),
            )
            .outerjoin(
                DocumentEmbedding,
                sa.and_(
                    DocumentEmbedding.document_id == Document.id, DocumentEmbedding.model == model
                ),
            )
            .outerjoin(
                UserPpr,
                sa.and_(UserPpr.document_id == Document.id, UserPpr.user_id == context.user_id),
            )
            .where(Document.id.in_(ids))
        )
    }
    ordered = [rows[document_id] for document_id in ids if document_id in rows]
    tags: defaultdict[int, list[tuple[int, float]]] = defaultdict(list)
    for document_id, topic_id, score in await session.execute(
        sa.select(DocumentTopic.document_id, DocumentTopic.topic_id, DocumentTopic.score)
        .where(DocumentTopic.document_id.in_(ids))
        .order_by(DocumentTopic.document_id, DocumentTopic.score.desc(), DocumentTopic.topic_id)
    ):
        tags[document_id].append((topic_id, score))

    def column(name: str) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
        return np.array([float(getattr(row, name)) for row in ordered], dtype=np.float64)

    evergreen = set(settings.evergreen_types)
    ages = [
        (now - (row.published_at or row.created_at)).total_seconds() / SECONDS_PER_DAY
        for row in ordered
    ]
    return CandidateSet(
        ids=[row.id for row in ordered],
        domain_ids=[row.domain_id for row in ordered],
        types=[row.type for row in ordered],
        published=[row.published_at is not None for row in ordered],
        tags=[tags[row.id] for row in ordered],
        signals=Signals(
            interest_cosine=column("interest"),
            topic_overlap=np.array(
                [context.interests.overlap(tags[row.id]) for row in ordered], dtype=np.float64
            ),
            ppr=column("ppr"),
            liked_cosine=column("liked"),
            hidden_cosine=column("hidden"),
            age_days=np.array(ages, dtype=np.float64),
            half_life_days=np.array(
                [
                    settings.evergreen_half_life_days
                    if row.type in evergreen
                    else settings.recency_half_life_days
                    for row in ordered
                ],
                dtype=np.float64,
            ),
            hidden_max_similarity=column("hidden_max"),
            query_cosine=column("query") if query is not None else None,
        ),
    )


@dataclass(frozen=True)
class Linkers:
    trusted: list[str]
    """Pinned domains linking to the document, most user PPR first."""
    other: list[str]
    """Other domains linking to it, most user PPR first."""


async def linkers(
    session: AsyncSession, context: UserContext, document_ids: Sequence[int]
) -> dict[int, Linkers]:
    """The other domains that link to each document, ranked by the user PPR of their linking
    documents."""
    source = aliased(Document)
    target = aliased(Document)
    mass = sa.func.sum(sa.func.coalesce(UserPpr.score, 0.0))
    rows = await session.execute(
        sa.select(target.id, source.domain_id, Domain.host, mass)
        .select_from(Link)
        .join(Url, Url.id == Link.dst_url_id)
        .join(target, target.id == Url.document_id)
        .join(source, source.id == Link.src_document_id)
        .join(Domain, Domain.id == source.domain_id)
        .outerjoin(
            UserPpr,
            sa.and_(UserPpr.document_id == source.id, UserPpr.user_id == context.user_id),
        )
        .where(target.id.in_(document_ids), source.domain_id != target.domain_id)
        .group_by(target.id, source.domain_id, Domain.host)
        .order_by(target.id, mass.desc(), Domain.host)
    )
    found: dict[int, Linkers] = {}
    for document_id, domain_id, host, _ in rows:
        entry = found.setdefault(document_id, Linkers([], []))
        (entry.trusted if domain_id in context.pinned else entry.other).append(host)
    return found


async def nearest_liked(
    session: AsyncSession, context: UserContext, document_ids: Sequence[int], model: str
) -> dict[int, LikedRef]:
    """The most similar recently liked document to each of `document_ids`."""
    if not context.liked:
        return {}
    candidate = aliased(DocumentEmbedding)
    liked = aliased(DocumentEmbedding)
    similarity = 1 - candidate.vector.cosine_distance(liked.vector)
    rows = await session.execute(
        sa.select(candidate.document_id, liked.document_id, Document.title, similarity)
        .distinct(candidate.document_id)
        .select_from(candidate)
        .join(liked, sa.and_(liked.model == model, liked.document_id.in_(context.liked)))
        .join(Document, Document.id == liked.document_id)
        .where(candidate.model == model, candidate.document_id.in_(document_ids))
        .order_by(candidate.document_id, similarity.desc())
    )
    return {
        document_id: LikedRef(document_id=liked_id, title=title, similarity=float(value))
        for document_id, liked_id, title, value in rows
    }


async def hosts(session: AsyncSession, domain_ids: Sequence[int]) -> dict[int, str]:
    rows = await session.execute(sa.select(Domain.id, Domain.host).where(Domain.id.in_(domain_ids)))
    return dict(rows.tuples().all())
