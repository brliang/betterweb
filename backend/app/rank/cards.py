"""What a feed or search card shows about a document (PLAN.md §9)."""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import Document, Domain, Url
from app.enums import DocumentType


class DocumentCard(BaseModel):
    id: int
    url: str
    domain: str
    type: DocumentType
    title: str | None
    author: str | None
    published_at: datetime | None
    excerpt: str | None


async def load_cards(session: AsyncSession, document_ids: Sequence[int]) -> dict[int, DocumentCard]:
    rows = await session.execute(
        sa.select(
            Document.id,
            Url.url,
            Domain.host,
            Document.type,
            Document.title,
            Document.author,
            Document.published_at,
            Document.excerpt,
        )
        .join(Url, Url.id == Document.canonical_url_id)
        .join(Domain, Domain.id == Document.domain_id)
        .where(Document.id.in_(document_ids))
    )
    return {
        row.id: DocumentCard(
            id=row.id,
            url=row.url,
            domain=row.host,
            type=row.type,
            title=row.title,
            author=row.author,
            published_at=row.published_at,
            excerpt=row.excerpt,
        )
        for row in rows
    }
