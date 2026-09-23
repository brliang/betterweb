"""LLM providers (PLAN.md §6.8). Callers depend on `LLMProvider`, never on a vendor."""

import logging
from typing import Protocol

from pydantic import BaseModel

from app.providers.openrouter import OpenRouterClient, ProviderError, Usage

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    @property
    def model(self) -> str: ...

    async def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        """One system + user turn; returns the reply text, stripped."""
        ...


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message
    finish_reason: str | None = None


class _ChatResponse(BaseModel):
    choices: list[_Choice]
    usage: Usage | None = None


class OpenRouterLLM:
    def __init__(self, client: OpenRouterClient, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        response = await self._client.post(
            "/chat/completions",
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
            },
            _ChatResponse,
        )
        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice is not None else None
        if choice is None or not text:
            raise ProviderError(f"{self._model} returned no text")
        if choice.finish_reason == "length":
            raise ProviderError(f"{self._model} hit max_tokens={max_tokens}")
        if response.usage is not None:
            logger.debug("%s: %s", self._model, response.usage)
        return text.strip()
