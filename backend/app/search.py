"""Search query embeddings (PLAN.md §6.6 "Candidate generation (search)").

A query is embedded once through the metered provider (the spend lands in the ledger as
`search`) and kept in a small in-memory cache, so paging through results costs nothing more.
Only the query text is sent: never a user identifier.
"""

from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Embedding
from app.enums import SpendPurpose
from app.providers.embeddings import EmbeddingProvider
from app.providers.spend import SpendMeter
from app.settings import Settings
from app.spend import open_meter, record_spend

ProviderFactory = Callable[[SpendMeter], EmbeddingProvider]


class QueryEmbedder:
    def __init__(self, settings: Settings, provider: ProviderFactory) -> None:
        self._settings = settings
        self._provider = provider
        self._cache: OrderedDict[str, Embedding] = OrderedDict()

    async def embed(self, session: AsyncSession, query: str, now: datetime) -> Embedding:
        """The query's embedding. Records what it cost in the session; the caller commits."""
        settings = self._settings
        key = f"{settings.embedding_model}\n{settings.search_query_instruction}\n{query}"
        if (cached := self._cache.get(key)) is not None:
            self._cache.move_to_end(key)
            return cached
        meter = await open_meter(session, settings, now)
        try:
            [vector] = await self._provider(meter).embed_queries(
                [query], settings.search_query_instruction
            )
        finally:
            await record_spend(session, meter, SpendPurpose.SEARCH)
        if settings.query_embedding_cache_size:
            self._cache[key] = vector
            while len(self._cache) > settings.query_embedding_cache_size:
                self._cache.popitem(last=False)
        return vector
