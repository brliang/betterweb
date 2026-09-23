"""Ranking logic that needs no database (PLAN.md §6.6, §6.7, milestone M7): components, feed
composition ratios and the diversity cap, topic adjacency, and "why this?" reasons."""

from collections import Counter

import numpy as np
import pytest

from app.enums import Slice
from app.rank.compose import FALLBACK, Pick, compose_page, slot_counts, slot_plan, spread
from app.rank.explain import (
    Breakdown,
    ComponentScore,
    Evidence,
    Exploration,
    LikedRef,
    ReasonKind,
    TopicRef,
    reasons,
)
from app.rank.scoring import (
    Component,
    Parameters,
    Signals,
    components,
    contributions,
    min_max,
    total,
    weight_table,
)
from app.rank.topics import Adjacency, Interests, TopicTree
from app.settings import RankingWeights

MAIN, SEMANTIC, GRAPH = Slice.MAIN, Slice.ADJACENT_SEMANTIC, Slice.ADJACENT_GRAPH


def array(*values: float) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
    return np.array(values, dtype=np.float64)


# Components


def test_min_max_rescales_within_the_set() -> None:
    assert min_max(array(2, 4, 6)).tolist() == [0, 0.5, 1]
    # No spread: positive values count fully, zeros not at all.
    assert min_max(array(3, 3)).tolist() == [1, 1]
    assert min_max(array(0, 0)).tolist() == [0, 0]
    assert min_max(array()).tolist() == []


def signals(n: int, **overrides: np.ndarray[tuple[int], np.dtype[np.float64]]) -> Signals:
    defaults = {
        "interest_cosine": np.zeros(n),
        "topic_overlap": np.zeros(n),
        "ppr": np.zeros(n),
        "liked_cosine": np.zeros(n),
        "hidden_cosine": np.zeros(n),
        "age_days": np.zeros(n),
        "half_life_days": np.full(n, 7.0),
        "hidden_max_similarity": np.zeros(n),
    }
    return Signals(**(defaults | overrides))


PARAMETERS = Parameters(topic_blend=0.5, documents=100, hide_threshold=0.75)


def test_recency_halves_every_half_life_and_ignores_future_dates() -> None:
    values = components(
        signals(4, age_days=array(0, 7, 14, -3), half_life_days=array(7, 7, 7, 7)), PARAMETERS
    )
    assert values[Component.RECENCY].tolist() == pytest.approx([1, 0.5, 0.25, 1])


def test_evergreen_types_decay_slower() -> None:
    values = components(signals(2, age_days=array(90, 90), half_life_days=array(7, 90)), PARAMETERS)
    assert values[Component.RECENCY][1] == pytest.approx(0.5)
    assert values[Component.RECENCY][0] < 1e-3


def test_hide_penalty_starts_at_the_threshold_on_an_absolute_scale() -> None:
    similarity = array(0.5, 0.75, 0.875, 1.0)
    values = components(signals(4, hidden_max_similarity=similarity), PARAMETERS)
    assert values[Component.HIDE].tolist() == pytest.approx([0, 0, 0.5, 1])
    # A slight resemblance stays slight: no min-max stretching to a full penalty.
    slight = components(signals(2, hidden_max_similarity=array(0.0, 0.8)), PARAMETERS)
    assert slight[Component.HIDE][1] == pytest.approx(0.2)


def test_ppr_is_log_scaled_before_normalizing() -> None:
    # In multiples of the uniform score 1/N: 0x, 1x, 10x, 100x.
    ppr = array(0, 0.01, 0.1, 1.0)
    value = components(signals(4, ppr=ppr), PARAMETERS)[Component.PPR]
    expected = np.log1p(ppr * 100) / np.log1p(100)
    assert value.tolist() == pytest.approx(expected.tolist())
    assert value[1] > 0.1  # a linear scale would leave everything but the top near 0


def test_interest_blends_embedding_and_topic_overlap() -> None:
    values = components(
        signals(3, interest_cosine=array(0.1, 0.3, -0.2), topic_overlap=array(1, 0, 0)),
        PARAMETERS,
    )
    # Cosines clip at 0, then rescale to [1/3, 1, 0]; the overlap is already [1, 0, 0].
    assert values[Component.INTEREST].tolist() == pytest.approx([0.5 / 3 + 0.5, 0.5, 0])


def test_feedback_is_liked_minus_hidden_clipped() -> None:
    values = components(
        signals(3, liked_cosine=array(0.6, 0.2, 0.1), hidden_cosine=array(0.2, 0.4, 0.1)),
        PARAMETERS,
    )
    assert values[Component.FEEDBACK].tolist() == pytest.approx([1, 0, 0])


def test_the_score_adds_contributions_and_subtracts_the_hide_penalty() -> None:
    values = components(
        signals(
            2,
            ppr=array(0.0, 1.0),
            age_days=array(0, 7),
            hidden_max_similarity=array(0, 1),
        ),
        PARAMETERS,
    )
    weights = weight_table(
        RankingWeights(interest=1, ppr=2, feedback=1, recency=1, hide=3), query_weight=5
    )
    contributed = contributions(values, weights)
    assert contributed[Component.HIDE].tolist() == [0, -3]
    assert total(contributed, 2).tolist() == pytest.approx([1.0, 2 + 0.5 - 3])
    assert Component.QUERY not in values


def test_search_adds_the_query_component() -> None:
    values = components(
        Signals(**{**signals(2).__dict__, "query_cosine": array(0.2, 0.6)}), PARAMETERS
    )
    assert values[Component.QUERY].tolist() == [0, 1]


# Composition


@pytest.mark.parametrize(
    ("page", "pct", "semantic", "expected"),
    [
        (20, 0.20, 0.5, (16, 2, 2)),
        (20, 0.10, 0.5, (18, 1, 1)),
        (20, 0.35, 0.5, (13, 4, 3)),
        (20, 0.20, 1.0, (16, 4, 0)),
        (20, 0.0, 0.5, (20, 0, 0)),
        (10, 0.20, 0.5, (8, 1, 1)),
    ],
)
def test_slot_counts_follow_the_exploration_share_and_split(
    page: int, pct: float, semantic: float, expected: tuple[int, int, int]
) -> None:
    counts = slot_counts(page, pct, semantic)
    assert (counts[MAIN], counts[SEMANTIC], counts[GRAPH]) == expected
    assert Counter(slot_plan(page, pct, semantic)) == {
        kind: count for kind, count in counts.items() if count
    }


def test_exploration_is_spread_through_the_page_not_appended() -> None:
    assert spread(20, 4) == [2, 7, 12, 17]
    assert spread(20, 1) == [10]
    plan = slot_plan(20, 0.20, 0.5)
    assert [i for i, kind in enumerate(plan) if kind != MAIN] == [2, 7, 12, 17]
    assert [plan[i] for i in (2, 7, 12, 17)] == [SEMANTIC, GRAPH, SEMANTIC, GRAPH]


def distinct_domains(n: int) -> list[int]:
    return list(range(n))


def test_a_full_page_has_the_planned_ratio() -> None:
    # Candidates 0-99 main, 100-199 semantic, 200-299 graph; all on different domains.
    queues = {
        MAIN: list(range(100)),
        SEMANTIC: list(range(100, 200)),
        GRAPH: list(range(200, 300)),
    }
    plan = slot_plan(20, 0.20, 0.5)
    page = compose_page(plan, queues, distinct_domains(300), max_per_domain=2)
    assert Counter(pick.slice for pick in page) == {MAIN: 16, SEMANTIC: 2, GRAPH: 2}
    assert [pick.slice for pick in page] == plan
    # Each slice in its own order.
    assert [p.candidate for p in page if p.slice == MAIN] == list(range(16))
    assert [p.candidate for p in page if p.slice == SEMANTIC] == [100, 101]


def test_at_most_two_per_domain_while_other_domains_have_candidates() -> None:
    # The best eight main candidates all come from domain 0.
    domains = [0] * 8 + list(range(1, 50))
    page = compose_page([MAIN] * 10, {MAIN: list(range(57))}, domains, max_per_domain=2)
    assert len(page) == 10
    assert Counter(domains[p.candidate] for p in page)[0] == 2
    assert [p.candidate for p in page][:2] == [0, 1]


def test_the_cap_gives_way_only_when_no_other_domain_is_left() -> None:
    domains = [0] * 6 + [1]
    page = compose_page([MAIN] * 5, {MAIN: list(range(7))}, domains, max_per_domain=2)
    # Two from domain 0, the one from domain 1, then domain 0 again to fill the page.
    assert [p.candidate for p in page] == [0, 1, 6, 2, 3]


def test_an_empty_slice_lends_its_slots() -> None:
    plan = slot_plan(10, 0.20, 0.5)
    page = compose_page(plan, {MAIN: list(range(20)), GRAPH: [50]}, distinct_domains(60), 2)
    # The semantic slot goes to the graph slice; the graph slot, now empty, to main.
    assert Counter(p.slice for p in page) == {MAIN: 9, GRAPH: 1}
    assert FALLBACK[SEMANTIC][1] == GRAPH
    only_exploration = compose_page([MAIN] * 3, {SEMANTIC: [1, 2], GRAPH: [3]}, [0, 1, 2, 3], 2)
    assert [(p.candidate, p.slice) for p in only_exploration] == [
        (1, SEMANTIC),
        (2, SEMANTIC),
        (3, GRAPH),
    ]


def test_a_candidate_in_several_queues_appears_once() -> None:
    page = compose_page(
        [MAIN, SEMANTIC, MAIN], {MAIN: [0, 1], SEMANTIC: [0, 2]}, [0, 1, 2], max_per_domain=2
    )
    assert page == [Pick(0, MAIN), Pick(2, SEMANTIC), Pick(1, MAIN)]


def test_a_page_is_short_only_when_every_queue_runs_out() -> None:
    page = compose_page(slot_plan(20, 0.2, 0.5), {MAIN: [0, 1]}, [0, 0], max_per_domain=2)
    assert [p.candidate for p in page] == [0, 1]


# Topics

# 1 Technology > 2 AI, 3 Hardware > 4 Chips; 5 Travel > 6 Europe; 7 Science
TREE = TopicTree(
    parents={1: None, 2: 1, 3: 1, 4: 3, 5: None, 6: 5, 7: None},
    names={
        1: "Technology",
        2: "AI",
        3: "Hardware",
        4: "Chips",
        5: "Travel",
        6: "Europe",
        7: "Science",
    },
)


def test_an_interest_covers_its_descendants() -> None:
    interests = Interests(TREE, {1: 1.0, 6: 2.0})
    assert interests.covering(4) == 1
    assert interests.covering(6) == 6
    assert interests.covering(5) is None
    assert interests.covered() == {1, 2, 3, 4, 6}
    assert interests.weight(4) == 1.0


def test_topic_overlap_is_tag_weighted_relative_to_the_strongest_interest() -> None:
    interests = Interests(TREE, {2: 1.0, 6: 2.0})
    assert interests.overlap([(6, 0.4)]) == 1.0
    assert interests.overlap([(2, 0.4)]) == 0.5
    assert interests.overlap([(2, 0.3), (7, 0.1)]) == pytest.approx(0.3 * 1 / (0.4 * 2))
    assert interests.overlap([]) == 0.0
    assert Interests(TREE, {}).overlap([(2, 0.3)]) == 0.0


def test_adjacent_topics_are_the_parent_and_siblings_with_their_descendants() -> None:
    adjacent = Interests(TREE, {2: 1.0}).adjacent()
    assert adjacent == {
        1: Adjacency(shown=1, interest=2),
        3: Adjacency(shown=3, interest=2),
        4: Adjacency(shown=3, interest=2),
    }
    # Tier-1 interests have no parent, so nothing is adjacent to them; covered topics never are.
    assert Interests(TREE, {5: 1.0}).adjacent() == {}
    assert 3 not in Interests(TREE, {2: 1.0, 3: 1.0}).adjacent()


# Reasons


def component(name: Component, contribution: float) -> ComponentScore:
    return ComponentScore(name=name, inputs={}, value=1.0, weight=1.0, contribution=contribution)


def explain(evidence: Evidence, *parts: tuple[Component, float]) -> list[tuple[ReasonKind, str]]:
    breakdown = Breakdown(components=[component(n, c) for n, c in parts], evidence=evidence)
    found = reasons(breakdown, max_reasons=3, min_contribution=0.1, examples=2)
    return [(reason.kind, reason.text) for reason in found]


EVIDENCE = Evidence(age_days=3.2, published=True)


def test_reasons_follow_the_largest_contributions() -> None:
    evidence = EVIDENCE.model_copy(
        update={
            "trusted_linkers": ["a.example", "b.example"],
            "trusted_linker_count": 3,
            "interest_topics": [TopicRef(id=2, name="Urban Planning")],
            "nearest_liked": LikedRef(document_id=9, title="Streets for people", similarity=0.8),
        }
    )
    assert explain(
        evidence,
        (Component.INTEREST, 0.9),
        (Component.PPR, 1.4),
        (Component.RECENCY, 0.05),
        (Component.FEEDBACK, 0.3),
        (Component.HIDE, -0.5),
    ) == [
        (ReasonKind.SOURCES, "Linked from 3 sites you trust (e.g. a.example, b.example)"),
        (ReasonKind.INTEREST, "Matches your interest: Urban Planning"),
        (ReasonKind.LIKED, "Similar to “Streets for people”, which you liked"),
    ]


@pytest.mark.parametrize(
    ("update", "text"),
    [
        ({"pinned_domain": "mine.example"}, "From mine.example, a site you pinned"),
        (
            {"trusted_linkers": ["a.example"], "trusted_linker_count": 1},
            "Linked from a.example, a site you trust",
        ),
        (
            {"other_linkers": ["c.example", "d.example", "e.example"], "other_linker_count": 3},
            "Linked from sites your sources link to (e.g. c.example, d.example)",
        ),
        ({}, "Close to your sources in the link graph"),
    ],
)
def test_the_sources_reason_names_the_linking_sites(update: dict[str, object], text: str) -> None:
    assert explain(EVIDENCE.model_copy(update=update), (Component.PPR, 1.0)) == [
        (ReasonKind.SOURCES, text)
    ]


def test_recency_and_search_reasons() -> None:
    assert explain(EVIDENCE, (Component.RECENCY, 0.5), (Component.QUERY, 5.0)) == [
        (ReasonKind.SEARCH, "Matches your search"),
        (ReasonKind.RECENCY, "Published 3 days ago"),
    ]
    found_today = EVIDENCE.model_copy(update={"age_days": 0.2, "published": False})
    assert explain(found_today, (Component.RECENCY, 0.5)) == [(ReasonKind.RECENCY, "Found today")]


@pytest.mark.parametrize(
    ("exploration", "text"),
    [
        (
            Exploration(
                slice=SEMANTIC,
                adjacent_topic=TopicRef(id=3, name="Hardware"),
                interest_topic=TopicRef(id=2, name="AI"),
            ),
            "Exploring: Hardware, next to your interest AI",
        ),
        (Exploration(slice=SEMANTIC), "Exploring: near your interests, past the closest matches"),
        (
            Exploration(
                slice=GRAPH, outside_topic=TopicRef(id=5, name="Travel"), path=["mine.example"]
            ),
            "Exploring: Travel, from a site mine.example links to",
        ),
        (
            Exploration(
                slice=GRAPH,
                outside_topic=TopicRef(id=5, name="Travel"),
                path=["mine.example", "friend.example"],
            ),
            "Exploring: Travel, 2 links away from mine.example",
        ),
    ],
)
def test_exploration_items_say_so_first(exploration: Exploration, text: str) -> None:
    evidence = EVIDENCE.model_copy(update={"exploration": exploration})
    found = explain(
        evidence, (Component.PPR, 1.0), (Component.INTEREST, 0.8), (Component.RECENCY, 0.5)
    )
    assert found[0] == (ReasonKind.EXPLORATION, text)
    assert [kind for kind, _ in found] == [
        ReasonKind.EXPLORATION,
        ReasonKind.SOURCES,
        ReasonKind.INTEREST,
    ]


def test_no_reason_from_weak_components() -> None:
    assert explain(EVIDENCE, (Component.PPR, 0.05), (Component.INTEREST, 0.0)) == []
