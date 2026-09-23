"""Metering of model provider spend (PLAN.md §6.4), for the hard monthly cap.

Providers call `SpendMeter.reserve` with an estimate before each request, which refuses the
request when it would not fit in the budget, and `SpendMeter.charge` with what it cost after.
The caller writes the charges (`SpendMeter.take`) to the web.provider_spend ledger in the same
transaction as the results they paid for; `app.spend` reads the ledger to size the budget.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.providers.openrouter import ProviderError


class SpendCapReached(ProviderError):
    """A request would take this month's provider spend past the cap."""


@dataclass(frozen=True)
class Charge:
    model: str
    tokens: int
    cost_usd: float
    estimated: bool
    """The provider reported no cost, so it was computed from the configured price."""


class SpendMeter:
    """Spend by this process against what is left of the month's budget."""

    def __init__(self, budget_usd: float) -> None:
        self._budget_usd = budget_usd
        self._spent_usd = 0.0
        self._pending: list[Charge] = []

    @property
    def spent_usd(self) -> float:
        return self._spent_usd

    @property
    def remaining_usd(self) -> float:
        return self._budget_usd - self._spent_usd

    def reserve(self, estimate_usd: float) -> None:
        """Raise SpendCapReached unless a request estimated at `estimate_usd` fits."""
        if estimate_usd > self.remaining_usd:
            raise SpendCapReached(
                f"a request estimated at ${estimate_usd:.6f} exceeds the ${self.remaining_usd:.6f} "
                "left of this month's provider budget (PROVIDER_MONTHLY_SPEND_CAP_USD)"
            )

    def charge(self, charge: Charge) -> None:
        self._spent_usd += charge.cost_usd
        self._pending.append(charge)

    def take(self) -> list[Charge]:
        """The charges not yet written to the ledger; clears them."""
        pending, self._pending = self._pending, []
        return pending


def estimate_tokens(texts: Sequence[str], chars_per_token: float) -> int:
    return math.ceil(sum(len(text) for text in texts) / chars_per_token)


def price(tokens: int, usd_per_mtok: float) -> float:
    return tokens * usd_per_mtok / 1_000_000
