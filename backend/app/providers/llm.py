"""LLM providers (PLAN.md §6.8). Callers depend on `LLMProvider`, never on a vendor."""

from typing import Protocol

from pydantic import BaseModel

from app.providers.openrouter import OpenRouterClient, ProviderError, Usage
from app.providers.spend import Charge, SpendMeter, estimate_tokens, price
from app.settings import Settings


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
    """Chat completions through OpenRouter.

    Every request goes through `meter`, which refuses it when its estimated cost (the prompt,
    plus `max_tokens` of reply) doesn't fit in the budget. A reply that turns out unusable is
    still charged: it was paid for.
    """

    def __init__(
        self, client: OpenRouterClient, settings: Settings, meter: SpendMeter, model: str
    ) -> None:
        self._client = client
        self._meter = meter
        self._model = model
        self._prices = settings.llm_prices[model]
        self._chars_per_token = settings.provider_chars_per_token

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        estimate = estimate_tokens([system, prompt], self._chars_per_token)
        self._meter.reserve(
            price(estimate, self._prices.input) + price(max_tokens, self._prices.output)
        )
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
        self._meter.charge(self._charge(response.usage, estimate, max_tokens))
        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice is not None else None
        if choice is None or not text:
            raise ProviderError(f"{self._model} returned no text")
        if choice.finish_reason == "length":
            raise ProviderError(f"{self._model} hit max_tokens={max_tokens}")
        return text.strip()

    def _charge(self, usage: Usage | None, estimate: int, max_tokens: int) -> Charge:
        """The request's cost as reported, else priced from its tokens (reported, or the
        estimate and a full reply)."""
        if usage is not None and usage.cost is not None:
            return Charge(self._model, usage.total_tokens, usage.cost, estimated=False)
        if usage is not None and usage.total_tokens:
            prompt, reply = usage.prompt_tokens, usage.completion_tokens
        else:
            prompt, reply = estimate, max_tokens
        cost = price(prompt, self._prices.input) + price(reply, self._prices.output)
        return Charge(self._model, prompt + reply, cost, estimated=True)
