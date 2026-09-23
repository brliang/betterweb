"""Dedup (PLAN.md §6.3): map each fetched URL to a Document.

A pipeline of strategies, asked in order; the first confident match wins. V0 has two:
1. the page's canonical URL already belongs to a document;
2. another document has exactly the same normalized text.
V1 adds a near-duplicate strategy (SimHash/MinHash) behind the same interface. Every change
of a URL's document is written to `web.dedup_decisions`, so strategies can be audited.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import DedupDecision, Document, Url

NEW_DOCUMENT = "new"
"""The method recorded when no strategy matched and the page became a document of its own."""
REDIRECT = "redirect"
"""The method recorded when a URL takes the document of the URL it redirects to."""
MAX_REDIRECT_CHAIN = 5
"""Redirect hops resolved per dedup pass (the fetch stage records one hop per fetch)."""


@dataclass(frozen=True)
class DedupCandidate:
    """A fetched page that is (or will become) a document."""

    url_id: int
    canonical_url_id: int
    """The page's declared canonical URL, or the page's own URL."""
    content_hash: str | None
    word_count: int


@dataclass(frozen=True)
class Match:
    document_id: int | None
    confidence: float
    details: dict[str, object] = field(default_factory=dict)


NO_MATCH = Match(None, 0.0)


class DedupStrategy(Protocol):
    @property
    def name(self) -> str:
        """Recorded as the decision's method."""
        ...

    async def match(self, session: AsyncSession, page: DedupCandidate) -> Match: ...


class CanonicalUrlStrategy:
    """The canonical URL already maps to a document (a re-crawl, or another URL of the same
    page that declares the same canonical)."""

    name = "canonical_url"

    async def match(self, session: AsyncSession, page: DedupCandidate) -> Match:
        document_id = await session.scalar(
            sa.select(Url.document_id).where(Url.id == page.canonical_url_id)
        )
        if document_id is None:
            return NO_MATCH
        return Match(document_id, 1.0, {"canonical_url_id": page.canonical_url_id})


class ContentHashStrategy:
    """Exactly the same normalized text (syndication, mirrors); the oldest document wins.
    Short texts never match: too many unrelated pages share them."""

    name = "content_hash"

    def __init__(self, min_words: int) -> None:
        self._min_words = min_words

    async def match(self, session: AsyncSession, page: DedupCandidate) -> Match:
        if page.content_hash is None or page.word_count < self._min_words:
            return NO_MATCH
        document_id = await session.scalar(
            sa.select(sa.func.min(Document.id)).where(Document.content_hash == page.content_hash)
        )
        if document_id is None:
            return NO_MATCH
        return Match(document_id, 1.0, {"content_hash": page.content_hash})


@dataclass(frozen=True)
class Decision:
    document_id: int | None
    """None: no strategy matched; the page becomes a new document."""
    method: str
    confidence: float
    details: dict[str, object]


async def decide(
    session: AsyncSession,
    page: DedupCandidate,
    strategies: Sequence[DedupStrategy],
    min_confidence: float,
) -> Decision:
    for strategy in strategies:
        match = await strategy.match(session, page)
        if match.document_id is not None and match.confidence >= min_confidence:
            return Decision(match.document_id, strategy.name, match.confidence, match.details)
    return Decision(None, NEW_DOCUMENT, 1.0, {})


async def assign(
    session: AsyncSession, url_ids: Sequence[int], document_id: int, decision: Decision
) -> int:
    """Map URLs to a document, logging a decision for each URL whose document changes;
    returns how many changed."""
    changed = (
        await session.scalars(
            sa.update(Url)
            .where(Url.id.in_(url_ids), Url.document_id.is_distinct_from(document_id))
            .values(document_id=document_id)
            .returning(Url.id)
        )
    ).all()
    session.add_all(
        DedupDecision(
            url_id=url_id,
            document_id=document_id,
            method=decision.method,
            confidence=decision.confidence,
            details=decision.details,
        )
        for url_id in changed
    )
    return len(changed)


RESOLVE_REDIRECTS = sa.text(
    "UPDATE web.urls s SET document_id = t.document_id FROM web.urls t "
    "WHERE s.redirect_to_url_id = t.id AND t.document_id IS NOT NULL "
    "AND s.document_id IS DISTINCT FROM t.document_id "
    "RETURNING s.id, t.document_id, t.id"
)


async def resolve_redirects(session: AsyncSession) -> int:
    """Give every URL that redirects the document of its target (PLAN.md §4.1), following
    chains up to MAX_REDIRECT_CHAIN hops; returns how many URLs changed document."""
    total = 0
    for _ in range(MAX_REDIRECT_CHAIN):
        rows = (await session.execute(RESOLVE_REDIRECTS)).tuples().all()
        if not rows:
            break
        session.add_all(
            DedupDecision(
                url_id=url_id,
                document_id=document_id,
                method=REDIRECT,
                confidence=1.0,
                details={"redirect_to_url_id": target_id},
            )
            for url_id, document_id, target_id in rows
        )
        total += len(rows)
    return total
