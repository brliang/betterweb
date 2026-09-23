"""The embed stage of a crawl cycle (PLAN.md §6.1 step 4, §6.4): embed new or changed
documents, then tag them with topics.

Documents are taken in id order, one embeddings request's worth (EMBEDDING_BATCH_SIZE) at a
time, and each batch is committed with its vectors, tags and spend. A document is a candidate
when it has no embedding from the configured model or was updated since its embedding was last
checked; a candidate whose embedding input is unchanged costs no request.

The stage stops early, leaving the remaining documents for the next cycle, when the monthly
provider budget runs out or the provider fails. Either way the cycle goes on.
"""

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.web import CrawlCycle, Document, DocumentEmbedding
from app.embed.inputs import embedding_input, input_hash
from app.embed.tagging import embedded_topic_count, tag_documents
from app.enums import SpendPurpose
from app.providers.embeddings import EmbeddingProvider
from app.providers.openrouter import ProviderError
from app.providers.spend import SpendCapReached, SpendMeter
from app.settings import Settings
from app.spend import record_spend

logger = logging.getLogger(__name__)

STATS_KEY = "embed"
STOPPED_SPEND_CAP = "spend_cap"
STOPPED_PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class _Candidate:
    document_id: int
    updated_at: datetime
    input: str
    input_hash: str
    embedded_hash: str | None
    """The hash of the text behind the document's current embedding, if it has one."""


class EmbedStage:
    def __init__(
        self,
        session: AsyncSession,
        cycle: CrawlCycle,
        provider: EmbeddingProvider,
        meter: SpendMeter,
        settings: Settings,
    ) -> None:
        self._session = session
        self._cycle = cycle
        self._provider = provider
        self._meter = meter
        self._settings = settings
        stage = cycle.stats.get(STATS_KEY)
        stage = stage if isinstance(stage, dict) else {}
        counts = stage.get("counts")
        self.counts: Counter[str] = Counter(counts if isinstance(counts, dict) else {})
        spend = stage.get("spend_usd")
        self.spend_usd = float(spend) if isinstance(spend, int | float) else 0.0
        self.stopped: str | None = None
        self._has_topics = False

    async def run(self) -> Counter[str]:
        self._has_topics = await embedded_topic_count(self._session, self._provider.model) > 0
        if not self._has_topics:
            # Documents are still embedded; `taxonomy embed` tags them all afterwards.
            logger.warning(
                "no topics embedded with %s: documents stay untagged", self._provider.model
            )
        after = 0
        while batch := await self._candidates(after):
            after = batch[-1].document_id
            try:
                await self._batch(batch)
            except SpendCapReached as error:
                self._stop(STOPPED_SPEND_CAP, error)
            except ProviderError as error:
                self._stop(STOPPED_PROVIDER_ERROR, error)
            await self._commit()
            if self.stopped:
                break
        if not self.counts:
            await self._commit()  # record that the stage ran
        logger.info(
            "embed stage: %s, $%.6f spent%s",
            dict(sorted(self.counts.items())),
            self.spend_usd,
            f", stopped ({self.stopped})" if self.stopped else "",
        )
        return self.counts

    async def _candidates(self, after: int) -> list[_Candidate]:
        """The next batch of documents to check, after document id `after`."""
        settings = self._settings
        embedding = sa.orm.aliased(DocumentEmbedding)
        rows = await self._session.execute(
            sa.select(
                Document.id,
                Document.updated_at,
                Document.title,
                Document.excerpt,
                # Just enough text for the input, plus a character to see a word cut in two.
                sa.func.left(Document.text, settings.embed_text_max_chars + 1),
                embedding.input_hash,
            )
            .outerjoin(
                embedding,
                sa.and_(
                    embedding.document_id == Document.id,
                    embedding.model == self._provider.model,
                ),
            )
            .where(
                Document.id > after,
                sa.or_(
                    embedding.document_id.is_(None),
                    embedding.document_updated_at != Document.updated_at,
                ),
            )
            .order_by(Document.id)
            .limit(settings.embedding_batch_size)
        )
        candidates = []
        for document_id, updated_at, title, excerpt, text, embedded_hash in rows:
            text_input = embedding_input(title, excerpt, text, settings.embed_text_max_chars)
            candidates.append(
                _Candidate(
                    document_id,
                    updated_at,
                    text_input,
                    input_hash(text_input),
                    embedded_hash,
                )
            )
        return candidates

    async def _batch(self, batch: Sequence[_Candidate]) -> None:
        todo = [c for c in batch if c.input and c.input_hash != c.embedded_hash]
        unchanged = [c for c in batch if c.input and c.input_hash == c.embedded_hash]
        # The request comes first, so a refused or failed one leaves nothing half-written.
        vectors = await self._provider.embed_documents([c.input for c in todo]) if todo else []
        # A document with nothing to embed is checked again next cycle, which costs no request.
        self.counts["empty"] += sum(1 for c in batch if not c.input)
        for candidate in unchanged:
            await self._session.execute(
                sa.update(DocumentEmbedding)
                .where(
                    DocumentEmbedding.document_id == candidate.document_id,
                    DocumentEmbedding.model == self._provider.model,
                )
                .values(document_updated_at=candidate.updated_at)
            )
        self.counts["unchanged"] += len(unchanged)
        if todo:
            await self._store(todo, vectors)

    async def _store(self, todo: Sequence[_Candidate], vectors: Sequence[list[float]]) -> None:
        session = self._session
        model = self._provider.model
        ids = [c.document_id for c in todo]
        statement = pg_insert(DocumentEmbedding).values(
            [
                {
                    "document_id": c.document_id,
                    "model": model,
                    "vector": vector,
                    "input_hash": c.input_hash,
                    "document_updated_at": c.updated_at,
                }
                for c, vector in zip(todo, vectors, strict=True)
            ]
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[DocumentEmbedding.document_id, DocumentEmbedding.model],
                set_={
                    "vector": statement.excluded.vector,
                    "input_hash": statement.excluded.input_hash,
                    "document_updated_at": statement.excluded.document_updated_at,
                    "created_at": sa.func.now(),
                },
            )
        )
        # A document keeps only its current model's embedding.
        await session.execute(
            sa.delete(DocumentEmbedding).where(
                DocumentEmbedding.document_id.in_(ids), DocumentEmbedding.model != model
            )
        )
        self.counts["embedded"] += len(todo)
        if self._has_topics:
            self.counts["tags"] += await tag_documents(session, model, self._settings, ids)
        else:
            self.counts["untagged"] += len(todo)

    def _stop(self, reason: str, error: ProviderError) -> None:
        self.stopped = reason
        log = logger.warning if reason == STOPPED_SPEND_CAP else logger.error
        log("embed stage stopped; the remaining documents wait for the next cycle: %s", error)

    async def _commit(self) -> None:
        self.spend_usd += await record_spend(
            self._session, self._meter, SpendPurpose.EMBED_DOCUMENTS, self._cycle.id
        )
        stats: dict[str, object] = {"counts": dict(self.counts), "spend_usd": self.spend_usd}
        if self.stopped:
            stats["stopped"] = self.stopped
        self._cycle.stats = {**self._cycle.stats, STATS_KEY: stats}
        await self._session.commit()


async def run_embed_stage(
    session: AsyncSession,
    cycle: CrawlCycle,
    provider: EmbeddingProvider,
    meter: SpendMeter,
    settings: Settings,
) -> Counter[str]:
    """Run (or resume) the embed stage over every document that needs it."""
    return await EmbedStage(session, cycle, provider, meter, settings).run()
