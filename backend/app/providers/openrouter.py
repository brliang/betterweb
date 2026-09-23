"""HTTP plumbing shared by the OpenRouter embedding and LLM providers (PLAN.md §6.4, §6.8).

Requests carry content only: never a user identifier, email or account ID.
"""

import asyncio
import logging
from types import TracebackType
from typing import Self

import httpx2
from pydantic import BaseModel

from app.settings import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ProviderError(Exception):
    """A model provider request failed, or returned something unusable."""


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float | None = None
    """USD, when OpenRouter reports it."""


class _ErrorBody(BaseModel):
    error: dict[str, object] | None = None


class OpenRouterClient:
    """An async client for OpenRouter's OpenAI-compatible API, with retries and backoff.

    Use as an async context manager so the connection pool is closed.
    """

    def __init__(
        self, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        key = settings.openrouter_api_key
        if key is None or not key.get_secret_value():
            raise ProviderError("OPENROUTER_API_KEY is not set (see .env.example)")
        self._max_retries = settings.provider_max_retries
        self._base_delay_s = settings.provider_retry_base_delay_s
        self._http = httpx2.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            timeout=settings.provider_timeout_s,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def post[T: BaseModel](self, path: str, payload: dict[str, object], into: type[T]) -> T:
        """POST JSON and parse the response into `into`, retrying transient failures."""
        for attempt in range(self._max_retries + 1):
            retries_left = attempt < self._max_retries
            try:
                response = await self._http.post(path, json=payload)
            except httpx2.TransportError as error:
                if not retries_left:
                    raise ProviderError(f"POST {path} failed: {error!r}") from error
                logger.warning("POST %s failed (%r); retrying", path, error)
            else:
                if response.status_code in RETRYABLE_STATUS and retries_left:
                    logger.warning("POST %s returned %d; retrying", path, response.status_code)
                elif response.is_error:
                    raise ProviderError(
                        f"POST {path} returned {response.status_code}: {response.text[:500]}"
                    )
                else:
                    # OpenRouter reports some failures (e.g. an upstream provider error) as a
                    # 200 with an `error` object instead of a result.
                    error_body = _ErrorBody.model_validate_json(response.content)
                    if error_body.error is not None:
                        raise ProviderError(f"POST {path} returned an error: {error_body.error}")
                    return into.model_validate_json(response.content)
            await asyncio.sleep(self._base_delay_s * 2**attempt)
        raise AssertionError("unreachable: the last attempt returns or raises")
