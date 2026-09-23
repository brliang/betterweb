"""Feed page composition (PLAN.md §6.6 "Feed composition", "Diversity").

A page of n slots gives round(ε·n) to exploration, split between the semantic and graph slices
by the user's split, and the rest to the main slice. Exploration slots are spread evenly
through the page, not appended. Each slot takes the best unused candidate of its slice with
fewer than MAX_PER_DOMAIN_PER_PAGE items from the same domain on the page; a slice that has
none left lends the slot to the next slice in `FALLBACK`. Only when no slice has a candidate
from another domain does the cap give way, so a young corpus still fills its pages.
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.enums import Slice

FALLBACK = {
    Slice.MAIN: (Slice.MAIN, Slice.ADJACENT_SEMANTIC, Slice.ADJACENT_GRAPH),
    Slice.ADJACENT_SEMANTIC: (Slice.ADJACENT_SEMANTIC, Slice.ADJACENT_GRAPH, Slice.MAIN),
    Slice.ADJACENT_GRAPH: (Slice.ADJACENT_GRAPH, Slice.ADJACENT_SEMANTIC, Slice.MAIN),
}
"""Where an empty slice's slot goes, in order."""


@dataclass(frozen=True)
class Pick:
    candidate: int
    """Index into the candidate arrays."""
    slice: Slice
    """The slice that supplied it (which may differ from the slot's)."""


def _round(value: float) -> int:
    """Half up, not Python's half to even: one exploration slot split 50/50 goes to the first
    slice either way."""
    return math.floor(value + 0.5)


def slot_counts(page_size: int, exploration_pct: float, semantic_share: float) -> dict[Slice, int]:
    explore = _round(page_size * exploration_pct)
    semantic = _round(explore * semantic_share)
    return {
        Slice.MAIN: page_size - explore,
        Slice.ADJACENT_SEMANTIC: semantic,
        Slice.ADJACENT_GRAPH: explore - semantic,
    }


def spread(page_size: int, count: int) -> list[int]:
    """`count` positions evenly spaced through a page, each in the middle of its stretch."""
    return [(2 * i + 1) * page_size // (2 * count) for i in range(count)]


def slot_plan(page_size: int, exploration_pct: float, semantic_share: float) -> list[Slice]:
    """The slice of each slot on a page."""
    counts = slot_counts(page_size, exploration_pct, semantic_share)
    semantic, graph = counts[Slice.ADJACENT_SEMANTIC], counts[Slice.ADJACENT_GRAPH]
    # Alternate the two exploration slices, starting with the larger one.
    first, second = (
        (Slice.ADJACENT_SEMANTIC, Slice.ADJACENT_GRAPH)
        if semantic >= graph
        else (Slice.ADJACENT_GRAPH, Slice.ADJACENT_SEMANTIC)
    )
    remaining = {first: max(semantic, graph), second: min(semantic, graph)}
    explore: list[Slice] = []
    while remaining[first] or remaining[second]:
        for kind in (first, second):
            if remaining[kind]:
                explore.append(kind)
                remaining[kind] -= 1
    plan = [Slice.MAIN] * page_size
    for position, kind in zip(spread(page_size, len(explore)), explore, strict=True):
        plan[position] = kind
    return plan


def compose_page(
    plan: Sequence[Slice],
    queues: Mapping[Slice, Sequence[int]],
    domains: Sequence[int],
    max_per_domain: int,
) -> list[Pick]:
    """Fill the slots of `plan` from the slices' candidate queues (best first).

    `domains[i]` is candidate i's domain. A candidate appears at most once, even if several
    queues hold it. Returns the page in order; it is shorter than the plan only when every
    queue runs out.
    """
    used: set[int] = set()
    per_domain: Counter[int] = Counter()
    slots: list[Pick | None] = [None] * len(plan)

    def take(kind: Slice, *, capped: bool) -> int | None:
        for candidate in queues.get(kind, ()):
            if candidate in used:
                continue
            if capped and per_domain[domains[candidate]] >= max_per_domain:
                continue
            return candidate
        return None

    for capped in (True, False):
        for position, preferred in enumerate(plan):
            if slots[position] is not None:
                continue
            for kind in FALLBACK[preferred]:
                candidate = take(kind, capped=capped)
                if candidate is not None:
                    slots[position] = Pick(candidate, kind)
                    used.add(candidate)
                    per_domain[domains[candidate]] += 1
                    break
    return [pick for pick in slots if pick is not None]
