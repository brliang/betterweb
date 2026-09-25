import asyncio

import pytest

from app.crawl.politeness import DeadlineReached, DomainGate, Pace, PrioritySlots

pytestmark = pytest.mark.anyio


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def gate(clock: FakeClock, pace: Pace | None = None, **overrides: float) -> DomainGate:
    options = {
        "delay_s": 2.0,
        "concurrency": 1,
        "backoff_max_s": 30.0,
        "max_errors": 3,
        "max_refusals": 3,
    }
    options.update(overrides)
    return DomainGate(
        pace=pace or Pace.fixed(options["delay_s"]),
        concurrency=int(options["concurrency"]),
        backoff_max_s=options["backoff_max_s"],
        max_errors=int(options["max_errors"]),
        max_refusals=int(options["max_refusals"]),
        clock=clock,
        sleep=clock.sleep,
    )


async def request_starts(
    domain: DomainGate, clock: FakeClock, durations: list[float], deadline: float | None = None
) -> list[float]:
    slots = PrioritySlots(10)
    starts = []
    for duration in durations:
        async with domain.turn(slots, 0, deadline):
            starts.append(clock.now)
            clock.now += duration
    return starts


async def test_requests_are_spaced_from_the_previous_finish() -> None:
    clock = FakeClock()
    assert await request_starts(gate(clock), clock, [0, 5, 0]) == [100, 102, 109]


async def test_errors_double_the_pause_up_to_the_cap() -> None:
    clock = FakeClock()
    domain = gate(clock, delay_s=2, backoff_max_s=10, max_errors=10)
    pauses = []
    for _ in range(4):
        start = clock.now
        domain.failed()
        await request_starts(domain, clock, [0])
        pauses.append(clock.now - start)
    assert pauses == [4, 8, 10, 10]


async def test_retry_after_is_honored_up_to_the_cap() -> None:
    clock = FakeClock()
    domain = gate(clock, backoff_max_s=60)
    domain.failed(retry_after_s=45)
    assert await request_starts(domain, clock, [0]) == [145]
    domain.failed(retry_after_s=3600)
    assert await request_starts(domain, clock, [0]) == [205]


async def test_consecutive_errors_exhaust_the_domain() -> None:
    domain = gate(FakeClock(), max_errors=3)
    domain.failed()
    domain.failed()
    domain.succeeded(0.1)
    domain.failed()
    domain.failed()
    assert not domain.exhausted
    domain.failed()
    assert domain.exhausted


ADAPTIVE = Pace(start_s=1, min_s=0.5, max_s=10, latency_factor=2)


def test_quick_answers_shorten_the_delay_down_to_the_floor() -> None:
    domain = gate(FakeClock(), ADAPTIVE)
    assert domain.delay_s == 1
    domain.succeeded(0.1)  # target 0.2, floored at 0.5: halfway from 1 is 0.75
    assert domain.delay_s == 0.75
    for _ in range(20):
        domain.succeeded(0.1)
    assert domain.delay_s == pytest.approx(0.5)


def test_slow_answers_lengthen_the_delay_up_to_the_cap() -> None:
    domain = gate(FakeClock(), ADAPTIVE)
    domain.succeeded(2)  # target 4
    assert domain.delay_s == 2.5
    for _ in range(20):
        domain.succeeded(30)
    assert domain.delay_s == pytest.approx(10)


async def test_the_adapted_delay_spaces_the_next_request() -> None:
    clock = FakeClock()
    domain = gate(clock, ADAPTIVE)
    slots = PrioritySlots(1)
    async with domain.turn(slots, 0):
        clock.now += 2
        domain.succeeded(2)
    async with domain.turn(slots, 0):
        assert clock.now == 104.5  # 2.5 s after the first request finished


def test_an_error_forgets_the_speed_up() -> None:
    domain = gate(FakeClock(), ADAPTIVE)
    for _ in range(20):
        domain.succeeded(0.1)
    domain.failed()
    assert domain.delay_s == 1
    domain = gate(FakeClock(), ADAPTIVE, backoff_max_s=0)
    domain.succeeded(2)
    domain.failed()
    assert domain.delay_s == 2.5  # already slower than the start


def test_refusals_never_speed_up_and_exhaust_the_domain() -> None:
    domain = gate(FakeClock(), ADAPTIVE, max_refusals=2)
    for _ in range(20):
        domain.succeeded(0.1)
    domain.refused()
    assert domain.delay_s == 1  # back to the start, however quick the refusal
    domain.succeeded(0.1)  # an answer in between starts the count over
    domain.refused()
    assert not domain.exhausted
    domain.refused()
    assert domain.exhausted


def test_a_crawl_delay_is_a_floor_even_above_the_cap() -> None:
    domain = gate(FakeClock(), ADAPTIVE)
    domain.require(3)
    assert domain.delay_s == 3
    domain.succeeded(0)
    assert domain.delay_s == 3
    domain.require(20)
    domain.succeeded(0)
    assert domain.delay_s == 20


@pytest.mark.parametrize(("learned", "expected"), [(None, 1), (0.6, 0.6), (0.1, 0.5), (60, 10)])
def test_a_learned_delay_is_where_the_gate_starts(learned: float | None, expected: float) -> None:
    domain = DomainGate(
        pace=ADAPTIVE, concurrency=1, backoff_max_s=0, max_errors=1, max_refusals=1, delay_s=learned
    )
    assert domain.delay_s == expected


async def test_a_turn_past_the_deadline_is_refused() -> None:
    clock = FakeClock()
    domain = gate(clock, delay_s=10)
    assert await request_starts(domain, clock, [0], deadline=105) == [100]
    with pytest.raises(DeadlineReached):
        await request_starts(domain, clock, [0], deadline=105)
    assert clock.now == 100  # refused without waiting


async def test_slots_admit_waiters_by_priority() -> None:
    slots = PrioritySlots(1)
    await slots.acquire(0)
    admitted: list[float] = []

    async def wait(priority: float) -> None:
        await slots.acquire(priority)
        admitted.append(priority)
        slots.release()

    tasks = [asyncio.create_task(wait(priority)) for priority in (1, 3, 2)]
    await asyncio.sleep(0)
    slots.release()
    await asyncio.gather(*tasks)
    assert admitted == [3, 2, 1]


async def test_cancelled_waiters_give_up_their_place() -> None:
    slots = PrioritySlots(1)
    await slots.acquire(0)
    high = asyncio.create_task(slots.acquire(5))
    low = asyncio.create_task(slots.acquire(1))
    await asyncio.sleep(0)
    high.cancel()
    await asyncio.sleep(0)
    slots.release()
    await asyncio.wait_for(low, timeout=1)
    assert high.cancelled()


async def test_a_slot_granted_to_a_cancelled_waiter_is_passed_on() -> None:
    slots = PrioritySlots(1)
    await slots.acquire(0)
    first = asyncio.create_task(slots.acquire(2))
    second = asyncio.create_task(slots.acquire(1))
    await asyncio.sleep(0)
    slots.release()  # grants `first`, which is cancelled before it runs
    first.cancel()
    await asyncio.wait_for(second, timeout=1)
