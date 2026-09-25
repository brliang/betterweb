"""Request pacing (PLAN.md §6.2): a global cap on concurrent requests, served in priority
order, and a gate per domain that spaces its requests, adapts the spacing to how fast the
domain answers, and backs off after errors."""

import asyncio
import heapq
import itertools
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Self

from app.settings import Settings


class DeadlineReached(Exception):
    """The domain's next turn would start after the stage's deadline."""


class PrioritySlots:
    """At most `size` holders at once; waiters are let in highest priority first."""

    def __init__(self, size: int) -> None:
        self._free = size
        self._waiters: list[tuple[float, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    async def acquire(self, priority: float) -> None:
        if self._free > 0 and not self._waiters:
            self._free -= 1
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (-priority, next(self._order), future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release()  # granted just as we were cancelled: pass the slot on
            raise

    def release(self) -> None:
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():  # skip waiters that were cancelled
                future.set_result(None)
                return
        self._free += 1


@dataclass(frozen=True)
class Pace:
    """How a DomainGate spaces requests: from `start_s`, each answered request moves the delay
    halfway toward `latency_factor` times how long it took, within `min_s` and `max_s`."""

    start_s: float
    min_s: float
    max_s: float
    latency_factor: float

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            start_s=settings.per_domain_start_delay_s,
            min_s=settings.per_domain_min_delay_s,
            max_s=settings.per_domain_max_delay_s,
            latency_factor=settings.per_domain_latency_factor,
        )

    @classmethod
    def fixed(cls, delay_s: float) -> Self:
        return cls(start_s=delay_s, min_s=delay_s, max_s=delay_s, latency_factor=1)


class DomainGate:
    """Spaces the requests to one domain and backs off when it signals overload.

    Each request starts at least `delay_s` after the previous one finished (or started, when
    `concurrency` > 1). The delay follows the domain's response times (see Pace), never below
    a floor that `require` raises for a robots.txt Crawl-delay. A 429, 5xx or timeout resets
    it to at least the starting delay and doubles the pause, up to `backoff_max_s` (a longer
    Retry-After is honored up to the same cap); after `max_errors` in a row the gate is
    exhausted and the domain is left alone for the rest of the cycle.
    """

    def __init__(
        self,
        *,
        pace: Pace,
        concurrency: int,
        backoff_max_s: float,
        max_errors: int,
        delay_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`delay_s` is where the last cycle left the domain's delay, if known."""
        self._pace = pace
        self.floor_s = pace.min_s
        self.delay_s = self._bounded(pace.start_s if delay_s is None else delay_s)
        self._backoff_max_s = backoff_max_s
        self._max_errors = max_errors
        self._clock = clock
        self._sleep = sleep
        self._slots = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._next_start = clock()
        self._errors = 0

    @property
    def exhausted(self) -> bool:
        return self._errors >= self._max_errors

    @asynccontextmanager
    async def turn(
        self, slots: PrioritySlots, priority: float, deadline: float | None = None
    ) -> AsyncIterator[None]:
        """Wait for this domain's next turn and a global slot, then hold both for one request.

        Raises DeadlineReached instead of waiting past `deadline` (on the gate's clock).
        """
        async with self._slots:
            async with self._lock:
                while (wait := self._next_start - self._clock()) > 0:
                    if deadline is not None and self._next_start > deadline:
                        raise DeadlineReached
                    await self._sleep(wait)
                if deadline is not None and self._clock() >= deadline:
                    raise DeadlineReached
                await slots.acquire(priority)
                self._next_start = self._clock() + self.delay_s
            try:
                yield
            finally:
                slots.release()
                self._next_start = max(self._next_start, self._clock() + self.delay_s)

    def require(self, delay_s: float) -> None:
        """Never go faster than one request per `delay_s` (a robots.txt Crawl-delay)."""
        self.floor_s = max(self.floor_s, delay_s)
        self.delay_s = self._bounded(self.delay_s)

    def succeeded(self, response_s: float) -> None:
        """The domain answered a request in `response_s` seconds."""
        self._errors = 0
        target = self._bounded(response_s * self._pace.latency_factor)
        self.delay_s = (self.delay_s + target) / 2

    def failed(self, retry_after_s: float | None = None) -> None:
        self._errors += 1
        self.delay_s = self._bounded(max(self.delay_s, self._pace.start_s))
        pause = max(retry_after_s or 0, self.delay_s * 2**self._errors)
        self._next_start = max(self._next_start, self._clock() + min(pause, self._backoff_max_s))

    def _bounded(self, delay_s: float) -> float:
        return min(max(delay_s, self.floor_s), max(self._pace.max_s, self.floor_s))
