"""Personalized PageRank against graphs small enough to solve by hand (PLAN.md §6.5, M6)."""

from fractions import Fraction

import numpy as np
import numpy.typing as npt
import pytest

from app.score.pagerank import Graph, blend, personalized_pagerank, seed_vector
from app.score.stage import domain_means, top_k

TOL = 1e-12


def graph(edges: list[tuple[int, int]], domains: list[int]) -> Graph:
    return Graph(
        documents=np.arange(100, 100 + len(domains), dtype=np.int64),
        domains=np.array(domains, dtype=np.int64),
        sources=np.array([s for s, _ in edges], dtype=np.intp),
        targets=np.array([t for _, t in edges], dtype=np.intp),
    )


def columns(*vectors: list[float]) -> npt.NDArray[np.float64]:
    return np.column_stack([np.array(v, dtype=np.float64) for v in vectors])


def solve(g: Graph, personalization: list[float], damping: float) -> npt.NDArray[np.float64]:
    result = personalized_pagerank(
        g, columns(personalization), damping=damping, tol=TOL, max_iter=1000
    )
    assert result.converged
    return result.scores[:, 0]


# 0 -> 1, 0 -> 2, 1 -> 2, 2 -> 0, 2 -> 3; node 3 has no outlinks.
DIAMOND = graph([(0, 1), (0, 2), (1, 2), (2, 0), (2, 3)], domains=[1, 1, 2, 3])


def test_hand_computed_graph() -> None:
    # Seed node 0, damping d = 1/2. With x = d·(links in) + (d·x3 + 1 - d)·[node is 0]:
    #   x1 = d·x0/2 = x0/4
    #   x2 = d·(x0/2 + x1) = 3·x0/8
    #   x3 = d·x2/2 = 3·x0/32
    #   x0 = d·x2/2 + d·x3 + 1/2 = 9·x0/64 + 1/2, so x0 = 32/55
    expected = [Fraction(32, 55), Fraction(8, 55), Fraction(12, 55), Fraction(3, 55)]
    scores = solve(DIAMOND, [1, 0, 0, 0], damping=0.5)
    assert scores == pytest.approx([float(x) for x in expected], abs=1e-9)


def test_two_nodes_at_the_default_damping() -> None:
    # A <-> B seeded on A: xA = d·xB + (1 - d), xB = d·xA, so xA = 1/(1 + d), xB = d/(1 + d).
    d = 0.85
    scores = solve(graph([(0, 1), (1, 0)], domains=[1, 1]), [1, 0], damping=d)
    assert scores == pytest.approx([1 / (1 + d), d / (1 + d)])


def test_a_cycle_under_plain_pagerank_is_uniform() -> None:
    ring = graph([(0, 1), (1, 2), (2, 0)], domains=[1, 1, 1])
    assert solve(ring, [1 / 3] * 3, damping=0.85) == pytest.approx([1 / 3] * 3)


def dense_solution(
    g: Graph, personalization: npt.NDArray[np.float64], damping: float
) -> npt.NDArray[np.float64]:
    """The same fixed point as a linear system: x = d·(W + p·dangling^T)·x + (1 - d)·p."""
    n = g.size
    outdegree = np.bincount(g.sources, minlength=n)
    walk = np.zeros((n, n))
    walk[g.targets, g.sources] = 1.0 / outdegree[g.sources]
    walk += np.outer(personalization, outdegree == 0)
    return np.linalg.solve(np.eye(n) - damping * walk, (1 - damping) * personalization)


def test_every_column_matches_a_dense_solve() -> None:
    rng = np.random.default_rng(6)
    n = 40
    # Nodes 0-4 link nowhere, so their surfers jump back to the seeds.
    pairs = {(int(s), int(t)) for s, t in rng.integers(0, n, size=(150, 2)) if s != t and s >= 5}
    g = graph(sorted(pairs), domains=[i % 5 for i in range(n)])
    seeds = [v for v in (seed_vector(g, [0]), seed_vector(g, [1, 3])) if v is not None]
    personalization = np.column_stack([blend(seeds, n), *seeds])

    result = personalized_pagerank(g, personalization, damping=0.85, tol=TOL, max_iter=1000)

    assert result.converged
    assert result.scores.sum(axis=0) == pytest.approx([1.0] * 3)
    for column in range(3):
        expected = dense_solution(g, personalization[:, column], 0.85)
        assert result.scores[:, column] == pytest.approx(expected, abs=1e-9)


def test_stopping_at_max_iter_is_reported() -> None:
    result = personalized_pagerank(
        DIAMOND, columns([1, 0, 0, 0]), damping=0.85, tol=TOL, max_iter=2
    )
    assert (result.iterations, result.converged) == (2, False)
    assert result.scores.sum() == pytest.approx(1.0)


def test_an_empty_graph() -> None:
    empty = graph([], domains=[])
    personalization = np.column_stack([blend([], 0)])
    result = personalized_pagerank(empty, personalization, damping=0.85, tol=TOL, max_iter=10)
    assert result.scores.shape == (0, 1)


def test_personalization_must_be_a_distribution() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        personalized_pagerank(DIAMOND, columns([1, 1, 0, 0]), damping=0.85, tol=TOL, max_iter=10)
    with pytest.raises(ValueError, match="4 nodes"):
        personalized_pagerank(DIAMOND, columns([1, 0]), damping=0.85, tol=TOL, max_iter=10)


def test_seeds_are_even_per_domain_then_per_document() -> None:
    # Domain 1 has two documents, domain 2 one; domain 9 has none and gets no share.
    vector = seed_vector(DIAMOND, [1, 2, 9])
    assert vector is not None
    assert vector.tolist() == pytest.approx([0.25, 0.25, 0.5, 0])
    assert seed_vector(DIAMOND, [9]) is None
    assert seed_vector(DIAMOND, []) is None


def test_blend_weights_users_equally() -> None:
    one, other = seed_vector(DIAMOND, [1]), seed_vector(DIAMOND, [2, 3])
    assert one is not None
    assert other is not None
    assert blend([one, other], 4).tolist() == pytest.approx([0.25, 0.25, 0.25, 0.25])
    assert blend([], 4).tolist() == [0.25] * 4


def test_top_k_keeps_the_highest_positive_scores() -> None:
    scores = np.array([0.1, 0.0, 0.4, 0.3, 0.2])
    assert sorted(top_k(scores, 2).tolist()) == [2, 3]
    assert sorted(top_k(scores, 10).tolist()) == [0, 2, 3, 4]


def test_domain_scores_are_the_mean_document_score() -> None:
    domain_ids, means = domain_means(DIAMOND, np.array([0.5, 0.1, 0.4, 0.0]))
    assert domain_ids == [1, 2]
    assert means == pytest.approx([0.3, 0.4])
