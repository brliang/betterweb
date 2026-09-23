"""Embedding providers (PLAN.md §6.4). Callers depend on `EmbeddingProvider`, never on a vendor."""

import logging
import math
from collections.abc import Sequence
from itertools import batched
from typing import Protocol

from pydantic import BaseModel

from app.db.base import EMBEDDING_DIMENSIONS, Embedding
from app.providers.openrouter import OpenRouterClient, ProviderError, Usage
from app.settings import Settings

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        """Embed passages (documents). Returns unit vectors, in input order."""
        ...

    async def embed_queries(self, texts: Sequence[str], instruction: str) -> list[Embedding]:
        """Embed texts that are matched against documents: search queries, or topics for
        tagging. `instruction` states the task, which instruction-tuned models such as Qwen3
        need on the query side only."""
        ...


def fit(vector: Sequence[float], dimensions: int) -> Embedding:
    """Truncate to `dimensions` and L2-normalize.

    Valid for Matryoshka-trained models such as Qwen3-Embedding, whose leading dimensions carry
    most of the meaning. Normalizing means cosine similarity is a dot product.
    """
    if len(vector) < dimensions:
        raise ProviderError(f"expected at least {dimensions} dimensions, got {len(vector)}")
    head = vector[:dimensions]
    norm = math.sqrt(math.fsum(x * x for x in head))
    if norm == 0:
        raise ProviderError("provider returned a zero vector")
    return [x / norm for x in head]


class _EmbeddingItem(BaseModel):
    index: int
    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    data: list[_EmbeddingItem]
    usage: Usage | None = None


class OpenRouterEmbeddings:
    """Embeddings through OpenRouter's /embeddings endpoint.

    OpenRouter doesn't pass a `dimensions` parameter through to Qwen3, so full-size vectors come
    back and are truncated here with `fit`.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        settings: Settings,
        *,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self._client = client
        self._model = settings.embedding_model
        self._batch_size = settings.embedding_batch_size
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        vectors: list[Embedding] = []
        for batch in batched(texts, self._batch_size):
            vectors.extend(await self._embed(batch))
        return vectors

    async def embed_queries(self, texts: Sequence[str], instruction: str) -> list[Embedding]:
        return await self.embed_documents(
            [f"Instruct: {instruction}\nQuery:{text}" for text in texts]
        )

    async def _embed(self, texts: Sequence[str]) -> list[Embedding]:
        response = await self._client.post(
            "/embeddings",
            {"model": self._model, "input": list(texts), "encoding_format": "float"},
            _EmbeddingResponse,
        )
        if sorted(item.index for item in response.data) != list(range(len(texts))):
            raise ProviderError(f"expected {len(texts)} embeddings, got {len(response.data)}")
        if response.usage is not None:
            logger.debug("embedded %d texts: %s", len(texts), response.usage)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [fit(item.embedding, self._dimensions) for item in ordered]
