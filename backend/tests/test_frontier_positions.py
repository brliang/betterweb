"""Depth limits and min-path merging (PLAN.md §6.2), without a database."""

import pytest

from app.crawl.frontier import SEED, Candidate, Position, merge_candidates
from app.settings import Settings

SETTINGS = Settings(_env_file=None, max_internal_depth=5, max_external_hops=1)


def test_following_links() -> None:
    assert SEED.follow(internal=True) == Position(1, 0)
    assert Position(3, 0).follow(internal=True) == Position(4, 0)
    # Leaving the domain costs a hop and restarts the depth count.
    assert Position(3, 0).follow(internal=False) == Position(0, 1)
    assert Position(2, 1).follow(internal=False) == Position(0, 2)


@pytest.mark.parametrize(
    ("position", "within"),
    [
        (Position(0, 0), True),
        (Position(5, 0), True),
        (Position(6, 0), False),
        (Position(5, 1), True),
        (Position(0, 2), False),
    ],
)
def test_limits(position: Position, within: bool) -> None:
    assert position.within(SETTINGS) is within


@pytest.mark.parametrize(
    ("paths", "merged"),
    [
        # One path: unchanged.
        ([Position(2, 1)], Position(2, 1)),
        # A shorter path within the domain wins.
        ([Position(4, 0), Position(1, 0)], Position(1, 0)),
        # Each field is the minimum over all paths, even if no single path achieves both.
        ([Position(0, 1), Position(3, 0)], Position(0, 0)),
        ([Position(5, 0), Position(2, 1), Position(4, 1)], Position(2, 0)),
        # Order doesn't matter.
        ([Position(1, 0), Position(4, 0)], Position(1, 0)),
    ],
)
def test_min_path_merging(paths: list[Position], merged: Position) -> None:
    assert Position(99, 99).merge(paths[0]) == paths[0]
    result = paths[0]
    for path in paths[1:]:
        result = result.merge(path)
    assert result == merged
    candidates = [Candidate("https://example.com/x", path) for path in paths]
    assert merge_candidates(candidates, SETTINGS) == {"https://example.com/x": merged}


def test_merging_can_bring_a_url_within_limits() -> None:
    too_deep = Candidate("https://example.com/x", Position(6, 0))
    shallow = Candidate("https://example.com/x", Position(0, 1))
    assert merge_candidates([too_deep], SETTINGS) == {}
    assert merge_candidates([too_deep, shallow], SETTINGS) == {
        "https://example.com/x": Position(0, 0)
    }


def test_urls_beyond_the_limits_are_dropped() -> None:
    candidates = [
        Candidate("https://example.com/deep", Position(6, 0)),
        Candidate("https://other.example/far", Position(0, 2)),
        Candidate("https://example.com/ok", Position(5, 1)),
    ]
    assert merge_candidates(candidates, SETTINGS) == {"https://example.com/ok": Position(5, 1)}


def test_account_pages_are_dropped() -> None:
    candidates = [
        Candidate("https://example.com/accounts/login/", Position(1, 0)),
        Candidate("https://example.com/signup/", Position(1, 0)),
        Candidate("https://example.com/posts/login-flows-explained/", Position(1, 0)),
    ]
    assert merge_candidates(candidates, SETTINGS) == {
        "https://example.com/posts/login-flows-explained/": Position(1, 0)
    }
