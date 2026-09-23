"""Ranking components and scores (PLAN.md §6.6), computed over a candidate set at once.

```
score(d, u) = w_int · interest + w_ppr · ppr + w_fb · feedback + w_rec · recency - w_hide · hide
search(d, u, q) = score(d, u) + w_q · query
```

Each component is in [0, 1]. Those whose raw scale depends on the model or the graph
(interest, ppr, feedback, query) are min-max normalized within the candidate set; recency and
the hide penalty already have an absolute [0, 1] scale, which min-max would distort (a slight
resemblance to a hidden document would become the full penalty).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from app.settings import RankingWeights

Scores = npt.NDArray[np.float64]


class Component(StrEnum):
    INTEREST = "interest"
    PPR = "ppr"
    FEEDBACK = "feedback"
    RECENCY = "recency"
    HIDE = "hide"
    QUERY = "query"


SUBTRACTED = frozenset({Component.HIDE})


@dataclass(frozen=True)
class Signals:
    """Raw per-candidate inputs, one entry per candidate. Cosines are 0 when either side has
    no vector."""

    interest_cosine: Scores
    """To the user's `interest` profile vector."""
    topic_overlap: Scores
    """Share of the document's topic tags inside the user's interests, weighted by interest
    weight relative to the strongest (see `app.rank.topics`)."""
    ppr: Scores
    """The user's personalized PageRank; 0 outside the stored top-K."""
    liked_cosine: Scores
    hidden_cosine: Scores
    """To the `liked` and `hidden` profile vectors."""
    age_days: Scores
    half_life_days: Scores
    hidden_max_similarity: Scores
    """Highest cosine to any recently hidden document."""
    query_cosine: Scores | None = None


@dataclass(frozen=True)
class Parameters:
    topic_blend: float
    """Share of topic overlap in the interest component."""
    documents: int
    """Documents in the graph: PPR is log-scaled in multiples of the uniform score 1/N."""
    hide_threshold: float


def min_max(values: Scores) -> Scores:
    """Rescale to [0, 1] within the set. A constant set has no spread to rescale, so every
    value becomes 1 if positive, else 0."""
    if len(values) == 0:
        return values.copy()
    low, high = float(values.min()), float(values.max())
    if high > low:
        return (values - low) / (high - low)
    return (values > 0).astype(np.float64)


def components(signals: Signals, parameters: Parameters) -> dict[Component, Scores]:
    """Every component's normalized value per candidate."""
    blend = parameters.topic_blend
    values = {
        Component.INTEREST: (1 - blend) * min_max(np.clip(signals.interest_cosine, 0, None))
        + blend * min_max(signals.topic_overlap),
        Component.PPR: min_max(np.log1p(signals.ppr * max(parameters.documents, 1))),
        Component.FEEDBACK: min_max(np.clip(signals.liked_cosine - signals.hidden_cosine, 0, None)),
        Component.RECENCY: np.power(
            0.5, np.clip(signals.age_days, 0, None) / signals.half_life_days
        ),
        Component.HIDE: np.clip(
            (signals.hidden_max_similarity - parameters.hide_threshold)
            / (1 - parameters.hide_threshold),
            0,
            1,
        ),
    }
    if signals.query_cosine is not None:
        values[Component.QUERY] = min_max(np.clip(signals.query_cosine, 0, None))
    return values


def weight_table(weights: RankingWeights, query_weight: float) -> dict[Component, float]:
    return {
        Component.INTEREST: weights.interest,
        Component.PPR: weights.ppr,
        Component.FEEDBACK: weights.feedback,
        Component.RECENCY: weights.recency,
        Component.HIDE: weights.hide,
        Component.QUERY: query_weight,
    }


def contributions(
    values: Mapping[Component, Scores], weights: Mapping[Component, float]
) -> dict[Component, Scores]:
    """Each component's signed share of the score: weight x value, negative for penalties."""
    return {
        name: (-1 if name in SUBTRACTED else 1) * weights[name] * value
        for name, value in values.items()
    }


def total(contributed: Mapping[Component, Scores], size: int) -> Scores:
    score = np.zeros(size)
    for value in contributed.values():
        score += value
    return score
