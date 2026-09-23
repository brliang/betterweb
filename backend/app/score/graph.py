"""Loading the document graph (PLAN.md §6.5) from `web.documents` and `web.links`.

Links point at URLs; an edge exists where the URL resolves to a document (`urls.document_id`),
so links to pages not crawled yet (or never to be) are dropped. Several links from one document
to URLs of the same document make one edge, and links from a document to itself none.
"""

import numpy as np
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import Document, Link, Url
from app.score.pagerank import Graph


async def load_graph(session: AsyncSession) -> Graph:
    documents = (
        await session.execute(sa.select(Document.id, Document.domain_id).order_by(Document.id))
    ).all()
    ids = np.array([row[0] for row in documents], dtype=np.int64)
    domains = np.array([row[1] for row in documents], dtype=np.int64)

    edges = (
        await session.execute(
            sa.select(Link.src_document_id, Url.document_id)
            .distinct()
            .join(Url, Url.id == Link.dst_url_id)
            .where(Url.document_id.is_not(None), Url.document_id != Link.src_document_id)
        )
    ).all()
    sources = np.searchsorted(ids, np.array([row[0] for row in edges], dtype=np.int64))
    targets = np.searchsorted(ids, np.array([row[1] for row in edges], dtype=np.int64))
    return Graph(ids, domains, sources, targets)
