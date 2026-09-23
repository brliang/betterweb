import pytest

from app.rank.topics import TopicTree
from app.summaries import SummaryInput, build_prompt, clean, order_interests

# 1 Travel > 2 Rail, 3 Hotels; 4 Food > 5 Baking.
TREE = TopicTree(
    parents={1: None, 2: 1, 3: 1, 4: None, 5: 4},
    names={1: "Travel", 2: "Rail", 3: "Hotels", 4: "Food", 5: "Baking"},
)


@pytest.mark.parametrize(
    ("weights", "document_topics", "limit", "expected"),
    [
        # A document on Rail falls under Travel (above it); Food doesn't, however strong.
        ({1: 1.0, 4: 2.0}, [2], 5, [1, 4]),
        # ...and under Rail itself; Hotels is only a sibling.
        ({2: 1.0, 3: 2.0}, [2], 5, [2, 3]),
        # A document on Travel falls under Rail too (below it).
        ({2: 1.0, 5: 2.0}, [1], 5, [2, 5]),
        # Strongest first within each group, then by name; capped; weight 0 dropped.
        ({3: 1.0, 2: 1.0, 4: 2.0, 5: 0.0}, [1], 2, [3, 2]),
        ({3: 1.0, 5: 2.0}, [], 5, [5, 3]),
    ],
)
def test_order_interests(
    weights: dict[int, float], document_topics: list[int], limit: int, expected: list[int]
) -> None:
    assert order_interests(TREE, weights, document_topics, limit) == expected


def test_build_prompt() -> None:
    given = SummaryInput(
        title="Night trains are back",
        domain="rail.example",
        excerpt="Sleeper routes return.",
        text=None,
        topics=["Travel > Rail"],
        interests=["Travel > Rail", "Food"],
    )
    assert build_prompt(given) == (
        "<page>\n"
        "Title: Night trains are back\n"
        "Site: rail.example\n"
        "Topics: Travel > Rail\n"
        "Excerpt: Sleeper routes return.\n"
        "</page>\n\n"
        "Their interests: Travel > Rail; Food"
    )
    bare = SummaryInput(None, "x.example", None, "Text.", [], [])
    assert build_prompt(bare) == (
        "<page>\nTitle: (untitled)\nSite: x.example\nOpening text: Text.\n</page>\n\n"
        "Their interests: (none chosen)"
    )


def test_clean() -> None:
    assert clean('  "You like\n trains."  ') == "You like trains."
