"""Personalized PageRank over the document graph (PLAN.md §6.5). Pure computation: `app.score.
graph` loads the graph and `app.score.stage` stores the scores.

A random surfer follows a link with probability `damping`, and otherwise jumps to a seed
document chosen from the personalization vector; a document with no outlinks sends the surfer
to the seeds too. A document's score is the share of time the surfer spends on it. Every column
of the personalization matrix is one such surfer (the global one and one per user), all solved
by the same power iteration.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy import sparse

Ids = npt.NDArray[np.int64]
Indices = npt.NDArray[np.intp]
Scores = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Graph:
    """Documents and the links between them, as node indices."""

    documents: Ids
    """Document IDs, ascending: node i is document `documents[i]`."""
    domains: Ids
    """The domain ID of each node."""
    sources: Indices
    targets: Indices
    """Edge i links node `sources[i]` to node `targets[i]`; no duplicates, no self-links."""

    @property
    def size(self) -> int:
        return len(self.documents)


def seed_vector(graph: Graph, domain_ids: Iterable[int]) -> Scores | None:
    """A personalization vector over the documents on `domain_ids`: each domain with documents
    gets an equal share, split evenly among its documents. None when none of the domains has a
    document yet."""
    on_seed_domain = np.isin(graph.domains, np.array(list(domain_ids), dtype=np.int64))
    if not on_seed_domain.any():
        return None
    domains, index, counts = np.unique(
        graph.domains[on_seed_domain], return_inverse=True, return_counts=True
    )
    vector = np.zeros(graph.size)
    vector[on_seed_domain] = 1.0 / (len(domains) * counts[index])
    return vector


def blend(vectors: list[Scores], size: int) -> Scores:
    """The mean of personalization vectors (each user weighted equally); uniform over every
    document, which is plain PageRank, when there are none."""
    if not vectors:
        return np.full(size, 1.0 / size) if size else np.zeros(0)
    return np.mean(vectors, axis=0)


@dataclass(frozen=True)
class PageRank:
    scores: npt.NDArray[np.float64]
    """One column per personalization column; each sums to 1."""
    iterations: int
    converged: bool


def personalized_pagerank(
    graph: Graph,
    personalization: npt.NDArray[np.float64],
    *,
    damping: float,
    tol: float,
    max_iter: int,
) -> PageRank:
    """Solve every column of `personalization` (nodes by surfers; each column sums to 1).

    Iterates until no column changes by more than `tol` (L1 norm) in one step, or `max_iter`.
    """
    n, surfers = personalization.shape
    if n != graph.size:
        raise ValueError(f"personalization has {n} rows for a graph of {graph.size} nodes")
    if n == 0 or surfers == 0:
        return PageRank(personalization.copy(), 0, True)
    if not np.allclose(personalization.sum(axis=0), 1.0):
        raise ValueError("every personalization column must sum to 1")
    outdegree = np.bincount(graph.sources, minlength=n)
    # walk[t, s] = 1 / outdegree(s) for each link s -> t: one step of the surfer.
    walk: sparse.csr_array[Any] = sparse.csr_array(
        (1.0 / outdegree[graph.sources], (graph.targets, graph.sources)), shape=(n, n)
    )
    dangling = outdegree == 0
    scores = personalization.copy()
    for iteration in range(1, max_iter + 1):
        stuck = scores[dangling].sum(axis=0)
        following: npt.NDArray[np.float64] = damping * (walk @ scores)
        updated = following + (damping * stuck + (1 - damping)) * personalization
        change = float(np.abs(updated - scores).sum(axis=0).max())
        scores = updated
        if change <= tol:
            return PageRank(scores, iteration, True)
    return PageRank(scores, max_iter, False)
