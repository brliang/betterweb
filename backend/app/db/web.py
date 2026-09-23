"""`web` schema: the shared web graph (PLAN.md §4.1).

Public information about the web only. Nothing here may reference `usr`; a test enforces it.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import WEB, Base, Embedding, JSONObject
from app.enums import CycleStatus, DocumentType, DomainStatus, FrontierReason

NOW = sa.func.now()
EMPTY_JSON = sa.text("'{}'::jsonb")
EMPTY_TEXT_ARRAY = sa.text("'{}'::text[]")


class Domain(Base):
    __tablename__ = "domains"
    __table_args__ = ({"schema": WEB},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    host: Mapped[str] = mapped_column(unique=True)
    """The URL authority as canonicalized (app.crawl.urls): lowercase, with the port only when
    it isn't the scheme's default."""
    status: Mapped[DomainStatus] = mapped_column(server_default=DomainStatus.ACTIVE.value)
    robots_txt: Mapped[str | None]
    robots_fetched_at: Mapped[datetime | None]
    crawl_delay_s: Mapped[float | None]
    """From robots.txt Crawl-delay, if set."""
    feed_urls: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), server_default=EMPTY_TEXT_ARRAY)
    sitemap_urls: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), server_default=EMPTY_TEXT_ARRAY)
    first_seen_at: Mapped[datetime] = mapped_column(server_default=NOW)
    last_crawled_at: Mapped[datetime | None]
    verified_owner_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    """Reserved for V1 publisher verification. Deliberately no FK yet."""


class Url(Base):
    __tablename__ = "urls"
    __table_args__ = ({"schema": WEB},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)
    """Canonicalized (PLAN.md §6.3) before insertion."""
    domain_id: Mapped[int] = mapped_column(sa.ForeignKey(Domain.id), index=True)
    document_id: Mapped[int | None] = mapped_column(
        # use_alter: documents and urls reference each other.
        sa.ForeignKey("web.documents.id", ondelete="SET NULL", use_alter=True),
        index=True,
    )
    """Null until the dedup stage maps this URL to a document."""
    http_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    etag: Mapped[str | None]
    last_modified: Mapped[str | None]
    """The Last-Modified header, verbatim, for conditional GET."""
    content_hash: Mapped[str | None]
    """SHA-256 of the last 200 response body; a different hash on re-fetch counts a change."""
    redirect_to_url_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("web.urls.id", ondelete="SET NULL"), index=True
    )
    """Where the last fetch redirected; the dedup stage maps this URL to the target's document."""
    first_seen_at: Mapped[datetime] = mapped_column(server_default=NOW)
    last_fetched_at: Mapped[datetime | None]
    """Set on every request that got an HTTP response or a network error; a URL that has
    been fetched and has no frontier entry is never enqueued again."""
    fetch_count: Mapped[int] = mapped_column(server_default="0")
    change_count: Mapped[int] = mapped_column(server_default="0")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = ({"schema": WEB},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    canonical_url_id: Mapped[int] = mapped_column(sa.ForeignKey(Url.id), unique=True)
    domain_id: Mapped[int] = mapped_column(sa.ForeignKey(Domain.id), index=True)
    type: Mapped[DocumentType]
    title: Mapped[str | None]
    author: Mapped[str | None]
    published_at: Mapped[datetime | None]
    language: Mapped[str | None]
    text: Mapped[str | None]
    """Extracted and normalized. Null for types with no text body (e.g. video metadata)."""
    excerpt: Mapped[str | None]
    word_count: Mapped[int | None]
    content_hash: Mapped[str | None] = mapped_column(index=True)
    """Hash of the normalized text; dedup strategy 2 matches on it."""
    simhash: Mapped[int | None] = mapped_column(sa.BigInteger)
    """Reserved for V1 near-duplicate detection."""
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW)


class Link(Base):
    """An edge from a document to a URL, which may not be crawled yet (PLAN.md §4.1)."""

    __tablename__ = "links"
    __table_args__ = ({"schema": WEB},)

    src_document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), primary_key=True
    )
    dst_url_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Url.id, ondelete="CASCADE"), primary_key=True, index=True
    )
    anchor_text: Mapped[str | None]
    is_internal: Mapped[bool]


class DocumentEmbedding(Base):
    __tablename__ = "document_embeddings"
    __table_args__ = ({"schema": WEB},)

    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(primary_key=True)
    """Stored so that changing the model triggers re-embedding."""
    vector: Mapped[Embedding]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class Topic(Base):
    """A category from the adapted IAB taxonomy (PLAN.md §6.4)."""

    __tablename__ = "topics"
    __table_args__ = ({"schema": WEB},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    external_id: Mapped[str] = mapped_column(unique=True)
    """The IAB taxonomy ID, or an `x-` ID for topics the adaptation adds."""
    name: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(sa.ForeignKey("web.topics.id"), index=True)
    tier: Mapped[int] = mapped_column(sa.SmallInteger)
    description: Mapped[str | None]
    embedding: Mapped[Embedding | None]
    embedding_model: Mapped[str | None]
    """The model that produced `embedding`; a different configured model means re-embedding."""


class DocumentTopic(Base):
    __tablename__ = "document_topics"
    __table_args__ = ({"schema": WEB},)

    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Topic.id, ondelete="CASCADE"), primary_key=True, index=True
    )
    score: Mapped[float]


class FrontierEntry(Base):
    """Crawl state for one URL (PLAN.md §6.2). A fetched URL keeps its entry, as a re-crawl,
    so its depth is known when its links are followed and it can be re-fetched."""

    __tablename__ = "frontier"
    __table_args__ = (
        sa.CheckConstraint("internal_depth >= 0", name="internal_depth_non_negative"),
        sa.CheckConstraint("external_hops >= 0", name="external_hops_non_negative"),
        {"schema": WEB},
    )

    url_id: Mapped[int] = mapped_column(sa.ForeignKey(Url.id, ondelete="CASCADE"), primary_key=True)
    internal_depth: Mapped[int] = mapped_column(sa.SmallInteger)
    external_hops: Mapped[int] = mapped_column(sa.SmallInteger)
    priority: Mapped[float] = mapped_column(server_default="0", index=True)
    reason: Mapped[FrontierReason]
    next_fetch_at: Mapped[datetime] = mapped_column(server_default=NOW)
    enqueued_at: Mapped[datetime] = mapped_column(server_default=NOW)
    cycle_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("web.crawl_cycles.id", ondelete="SET NULL"), index=True
    )
    """The cycle whose plan includes this URL; cleared when it is fetched. A killed cycle
    resumes by fetching the entries still marked with it."""
    failures: Mapped[int] = mapped_column(sa.SmallInteger, server_default="0")
    """Transient fetch failures in a row (429, 5xx, timeouts); drives the retry backoff."""


class DedupDecision(Base):
    """Append-only log of every URL-to-document merge (PLAN.md §4.1)."""

    __tablename__ = "dedup_decisions"
    __table_args__ = ({"schema": WEB},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    url_id: Mapped[int] = mapped_column(sa.ForeignKey(Url.id, ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), index=True
    )
    method: Mapped[str]
    """Name of the DedupStrategy that matched. An open set, so not an enum."""
    confidence: Mapped[float]
    details: Mapped[JSONObject] = mapped_column(server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class CrawlCycle(Base):
    __tablename__ = "crawl_cycles"
    __table_args__ = ({"schema": WEB},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(server_default=NOW)
    finished_at: Mapped[datetime | None]
    status: Mapped[CycleStatus] = mapped_column(server_default=CycleStatus.RUNNING.value)
    page_budget: Mapped[int]
    pages_fetched: Mapped[int] = mapped_column(server_default="0")
    stats: Mapped[JSONObject] = mapped_column(server_default=EMPTY_JSON)
    """Per-stage counts, timings, errors and provider spend."""


class RawPage(Base):
    """A fetched body waiting for the extract stage (PLAN.md §6.1 step 2), which deletes it.

    Only new or changed content is stored: a 304 or an unchanged hash stores nothing.
    """

    __tablename__ = "raw_pages"
    __table_args__ = ({"schema": WEB},)

    url_id: Mapped[int] = mapped_column(sa.ForeignKey(Url.id, ondelete="CASCADE"), primary_key=True)
    cycle_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey(CrawlCycle.id, ondelete="SET NULL"), index=True
    )
    fetched_at: Mapped[datetime]
    content_type: Mapped[str]
    """The media type from the Content-Type header, lowercase, without parameters."""
    charset: Mapped[str | None]
    """The charset parameter of the Content-Type header, if any."""
    robots_tag: Mapped[str | None]
    """X-Robots-Tag headers, one per line; `noindex` and `nofollow` are honored."""
    body: Mapped[bytes] = mapped_column(sa.LargeBinary)


class GlobalScore(Base):
    """Latest global PageRank per document; drives crawl priority (PLAN.md §6.5)."""

    __tablename__ = "global_scores"
    __table_args__ = ({"schema": WEB},)

    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), primary_key=True
    )
    cycle_id: Mapped[int] = mapped_column(sa.ForeignKey(CrawlCycle.id), index=True)
    """The cycle that computed this score."""
    pagerank: Mapped[float]


class DomainScore(Base):
    """Latest aggregate score per domain; used as the domain prior."""

    __tablename__ = "domain_scores"
    __table_args__ = ({"schema": WEB},)

    domain_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Domain.id, ondelete="CASCADE"), primary_key=True
    )
    cycle_id: Mapped[int] = mapped_column(sa.ForeignKey(CrawlCycle.id), index=True)
    score: Mapped[float]


class DomainMetadataOverride(Base):
    """Reserved for V1 publisher controls. Empty in V0, but ingestion already applies it."""

    __tablename__ = "domain_metadata_overrides"
    __table_args__ = ({"schema": WEB},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    domain_id: Mapped[int] = mapped_column(sa.ForeignKey(Domain.id, ondelete="CASCADE"), index=True)
    url_pattern: Mapped[str]
    field: Mapped[str]
    value: Mapped[object] = mapped_column(JSONB)
    """Any JSON value: the replacement for `field`."""
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
