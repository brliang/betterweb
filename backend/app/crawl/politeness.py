"""Request pacing (PLAN.md §6.2): a global cap on concurrent requests, served in priority
order, and a gate per domain that spaces its requests and backs off after errors."""

import asyncio
import heapq
import itertools
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager


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


class DomainGate:
    """Spaces the requests to one domain and backs off when it signals overload.

    Each request starts at least `delay_s` after the previous one finished (or started, when
    `concurrency` > 1). A 429, 5xx or timeout doubles the pause, up to `backoff_max_s` (a longer
    Retry-After is honored up to the same cap); after `max_errors` in a row the gate is
    exhausted and the domain is left alone for the rest of the cycle.
    """

    def __init__(
        self,
        *,
        delay_s: float,
        concurrency: int,
        backoff_max_s: float,
        max_errors: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.delay_s = delay_s
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

    def succeeded(self) -> None:
        self._errors = 0

    def failed(self, retry_after_s: float | None = None) -> None:
        self._errors += 1
        pause = max(retry_after_s or 0, self.delay_s * 2**self._errors)
        self._next_start = max(self._next_start, self._clock() + min(pause, self._backoff_max_s))
