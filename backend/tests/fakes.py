"""Deterministic stand-ins for model providers, so tests never call a real one."""

import hashlib
import math
import random
from collections.abc import Sequence

from app.db.base import EMBEDDING_DIMENSIONS, Embedding


def unit_vector(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> Embedding:
    """The same text always gives the same random unit vector."""
    rng = random.Random(hashlib.sha256(text.encode()).digest())  # noqa: S311 (not crypto)
    vector = [rng.gauss(0, 1) for _ in range(dimensions)]
    norm = math.sqrt(math.fsum(x * x for x in vector))
    return [x / norm for x in vector]


class FakeEmbeddings:
    def __init__(self, model: str = "fake-embedding") -> None:
        self._model = model
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS

    async def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        self.calls.append(list(texts))
        return [unit_vector(text) for text in texts]

    async def embed_queries(self, texts: Sequence[str], instruction: str) -> list[Embedding]:
        return await self.embed_documents([f"{instruction}: {text}" for text in texts])


class FakeLLM:
    def __init__(self, reply: str = "A description.") -> None:
        self._reply = reply
        self.prompts: list[str] = []

    @property
    def model(self) -> str:
        return "fake-llm"

    async def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        self.prompts.append(prompt)
        return self._reply
