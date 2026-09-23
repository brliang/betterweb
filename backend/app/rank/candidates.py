"""Candidate generation (PLAN.md §6.6). Each source returns document IDs, best first, that
pass the hard filters; the ranker scores their union.

- main: the top of the user's PPR, the nearest documents to the `interest` and `liked` profile
  vectors, and the most recent documents on pinned domains.
- adjacent_semantic: documents in a middle band of similarity to the `interest` vector (past
  the EXPLORE_BAND_SKIP nearest), and documents tagged with a topic adjacent to an interest
  (its parent or a sibling) and with none the interests cover.
- adjacent_graph: documents on domains 1 to EXPLORE_GRAPH_MAX_HOPS domain-level links from a
  pinned domain, tagged only with topics outside the user's interests.
- search: the nearest documents to the query embedding.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.base import Embedding
from app.db.usr import UserPpr
from app.db.web import Document, DocumentEmbedding, DocumentTopic, Link, Url
from app.rank.context import UserContext
from app.rank.topics import Adjacency

Filters = Sequence[sa.ColumnElement[bool]]

RECENT = sa.func.coalesce(Document.published_at, Document.created_at)


async def enable_filtered_vector_search(session: AsyncSession) -> None:
    """Let HNSW scans keep going until a filtered query has its LIMIT (pgvector 0.8), instead
    of stopping at ef_search rows and returning too few; for this transaction only."""
    await session.execute(sa.text("SET LOCAL hnsw.iterative_scan = relaxed_order"))


def _in_order(rows: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(rows))


async def ppr_top(
    session: AsyncSession, context: UserContext, filters: Filters, n: int
) -> list[int]:
    rows = await session.scalars(
        sa.select(Document.id)
        .join(UserPpr, UserPpr.document_id == Document.id)
        .where(UserPpr.user_id == context.user_id, *filters)
        .order_by(UserPpr.score.desc(), Document.id)
        .limit(n)
    )
    return list(rows)


async def nearest(
    session: AsyncSession,
    vector: Embedding,
    model: str,
    filters: Filters,
    n: int,
    *,
    skip: int = 0,
) -> list[int]:
    """Documents by cosine similarity to `vector`, from the `skip`-th nearest on."""
    distance = DocumentEmbedding.vector.cosine_distance(vector)
    rows = await session.scalars(
        sa.select(Document.id)
        .join(DocumentEmbedding, DocumentEmbedding.document_id == Document.id)
        .where(DocumentEmbedding.model == model, *filters)
        .order_by(distance)  # the distance alone, so the HNSW index can serve it
        .offset(skip)
        .limit(n)
    )
    return _in_order(rows)


async def recent_pinned(
    session: AsyncSession, context: UserContext, filters: Filters, n: int
) -> list[int]:
    if not context.pinned:
        return []
    rows = await session.scalars(
        sa.select(Document.id)
        .where(Document.domain_id.in_(context.pinned), *filters)
        .order_by(RECENT.desc(), Document.id)
        .limit(n)
    )
    return list(rows)


def _untouched_by(covered: set[int]) -> sa.ColumnElement[bool]:
    """The document has no tag the user's interests cover."""
    tag = aliased(DocumentTopic)  # the outer query may join DocumentTopic itself
    return ~sa.exists().where(tag.document_id == Document.id, tag.topic_id.in_(covered))


async def topic_adjacent(
    session: AsyncSession,
    adjacent: dict[int, Adjacency],
    covered: set[int],
    filters: Filters,
    n: int,
) -> dict[int, Adjacency]:
    """Documents tagged with an adjacent topic and none the interests cover, strongest tag
    first, each with why it is adjacent."""
    if not adjacent:
        return {}
    rows = await session.execute(
        sa.select(Document.id, DocumentTopic.topic_id)
        .join(DocumentTopic, DocumentTopic.document_id == Document.id)
        .where(DocumentTopic.topic_id.in_(adjacent), _untouched_by(covered), *filters)
        .order_by(DocumentTopic.score.desc(), RECENT.desc(), Document.id)
        .limit(n * 3)  # a document can have several adjacent tags
    )
    found: dict[int, Adjacency] = {}
    for document_id, topic_id in rows:
        if document_id not in found and len(found) < n:
            found[document_id] = adjacent[topic_id]
    return found


async def domain_reach(
    session: AsyncSession, pinned: Iterable[int], max_hops: int
) -> dict[int, list[int]]:
    """Domains reachable from the pinned ones by following cross-domain links, up to
    `max_hops` domain-level steps: domain ID -> the path of domain IDs from a pinned domain to
    the one linking to it (shortest; ties by lowest ID)."""
    paths: dict[int, list[int]] = {domain: [] for domain in pinned}
    layer = sorted(paths)
    for _ in range(max_hops):
        if not layer:
            break
        rows = await session.execute(
            sa.select(Document.domain_id, Url.domain_id)
            .distinct()
            .join(Link, Link.src_document_id == Document.id)
            .join(Url, Url.id == Link.dst_url_id)
            .where(Document.domain_id.in_(layer), Url.domain_id != Document.domain_id)
            .order_by(Document.domain_id, Url.domain_id)
        )
        next_layer = []
        for source, target in rows:
            if target not in paths:
                paths[target] = [*paths[source], source]
                next_layer.append(target)
        layer = next_layer
    return {domain: path for domain, path in paths.items() if path}


@dataclass(frozen=True)
class GraphRoute:
    path: list[int]
    """Domain IDs from a pinned domain to the one linking to the document's domain."""


async def graph_adjacent(
    session: AsyncSession,
    context: UserContext,
    covered: set[int],
    filters: Filters,
    n: int,
    max_hops: int,
) -> dict[int, GraphRoute]:
    """Tagged documents on domains near the pins with no tag the interests cover, highest
    user PPR first, then newest."""
    reach = await domain_reach(session, context.pinned, max_hops)
    if not reach:
        return {}
    ppr = (
        sa.select(UserPpr.score)
        .where(UserPpr.user_id == context.user_id, UserPpr.document_id == Document.id)
        .scalar_subquery()
    )
    rows = await session.execute(
        sa.select(Document.id, Document.domain_id)
        .where(
            Document.domain_id.in_(reach),
            sa.exists().where(DocumentTopic.document_id == Document.id),
            _untouched_by(covered),
            *filters,
        )
        .order_by(sa.func.coalesce(ppr, 0).desc(), RECENT.desc(), Document.id)
        .limit(n)
    )
    return {document_id: GraphRoute(reach[domain_id]) for document_id, domain_id in rows}
