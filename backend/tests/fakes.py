"""Deterministic stand-ins for model providers, so tests never call a real one."""

import hashlib
import math
import random
from collections.abc import Sequence

from app.db.base import EMBEDDING_DIMENSIONS, Embedding
from app.providers.openrouter import ProviderError
from app.providers.spend import Charge, SpendMeter


def unit_vector(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> Embedding:
    """The same text always gives the same random unit vector."""
    rng = random.Random(hashlib.sha256(text.encode()).digest())  # noqa: S311 (not crypto)
    vector = [rng.gauss(0, 1) for _ in range(dimensions)]
    norm = math.sqrt(math.fsum(x * x for x in vector))
    return [x / norm for x in vector]


class FakeEmbeddings:
    """Charges `usd_per_text` per text to `meter`, if given, like a real provider would; raises
    `error` instead of answering while it is set."""

    def __init__(
        self,
        model: str = "fake-embedding",
        *,
        meter: SpendMeter | None = None,
        usd_per_text: float = 0.0,
    ) -> None:
        self._model = model
        self._meter = meter
        self._usd_per_text = usd_per_text
        self.error: ProviderError | None = None
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS

    async def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        cost = self._usd_per_text * len(texts)
        if self._meter is not None:
            self._meter.reserve(cost)
        if self.error is not None:
            raise self.error
        self.calls.append(list(texts))
        if self._meter is not None:
            self._meter.charge(Charge(self._model, len(texts), cost, estimated=False))
        return [unit_vector(text) for text in texts]

    async def embed_queries(self, texts: Sequence[str], instruction: str) -> list[Embedding]:
        return await self.embed_documents([f"{instruction}: {text}" for text in texts])


class FakeLLM:
    """Replies `reply` to every prompt, charging `usd_per_call` to `meter`, if given, like a
    real provider would; raises `error` instead of answering while it is set."""

    def __init__(
        self,
        reply: str = "A description.",
        *,
        model: str = "fake-llm",
        meter: SpendMeter | None = None,
        usd_per_call: float = 0.0,
    ) -> None:
        self._reply = reply
        self._model = model
        self._meter = meter
        self._usd_per_call = usd_per_call
        self.error: ProviderError | None = None
        self.systems: list[str] = []
        self.prompts: list[str] = []

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        if self._meter is not None:
            self._meter.reserve(self._usd_per_call)
        if self.error is not None:
            raise self.error
        self.systems.append(system)
        self.prompts.append(prompt)
        if self._meter is not None:
            self._meter.charge(Charge(self._model, 1, self._usd_per_call, estimated=False))
        return self._reply
