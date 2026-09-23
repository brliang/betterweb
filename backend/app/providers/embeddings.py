"""Embedding providers (PLAN.md §6.4). Callers depend on `EmbeddingProvider`, never on a vendor."""

import math
from collections.abc import Sequence
from itertools import batched
from typing import Protocol

from pydantic import BaseModel

from app.db.base import EMBEDDING_DIMENSIONS, Embedding
from app.providers.openrouter import OpenRouterClient, ProviderError, Usage
from app.providers.spend import Charge, SpendMeter, estimate_tokens, price
from app.settings import Settings


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
    back and are truncated here with `fit`. Every request goes through `meter`, which refuses
    it when its estimated cost doesn't fit in the month's remaining budget.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        settings: Settings,
        meter: SpendMeter,
        *,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self._client = client
        self._meter = meter
        self._model = settings.embedding_model
        self._batch_size = settings.embedding_batch_size
        self._usd_per_mtok = settings.embedding_usd_per_mtok
        self._chars_per_token = settings.provider_chars_per_token
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
        estimate = estimate_tokens(texts, self._chars_per_token)
        self._meter.reserve(price(estimate, self._usd_per_mtok))
        response = await self._client.post(
            "/embeddings",
            {"model": self._model, "input": list(texts), "encoding_format": "float"},
            _EmbeddingResponse,
        )
        self._meter.charge(self._charge(response.usage, estimate))
        if sorted(item.index for item in response.data) != list(range(len(texts))):
            raise ProviderError(f"expected {len(texts)} embeddings, got {len(response.data)}")
        ordered = sorted(response.data, key=lambda item: item.index)
        return [fit(item.embedding, self._dimensions) for item in ordered]

    def _charge(self, usage: Usage | None, estimate: int) -> Charge:
        """The request's cost as reported, else priced from its tokens (reported or estimated)."""
        if usage is not None and usage.cost is not None:
            return Charge(self._model, usage.prompt_tokens, usage.cost, estimated=False)
        tokens = usage.prompt_tokens if usage is not None and usage.prompt_tokens else estimate
        return Charge(self._model, tokens, price(tokens, self._usd_per_mtok), estimated=True)
