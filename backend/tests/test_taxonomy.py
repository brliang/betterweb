"""The adapted taxonomy (PLAN.md §6.4): adaptation rules, and the committed data files."""

import hashlib

import pytest

from app.taxonomy import (
    ADDED_ID_PREFIX,
    MAX_TIER,
    TAXONOMY_DIR,
    Adaptation,
    Description,
    TaxonomyError,
    TopicSpec,
    adapt,
    load_adaptation,
    load_descriptions,
    load_taxonomy,
    read_iab,
    stale_descriptions,
)

SOURCE = [
    TopicSpec("1", "Tech", None, 1),
    TopicSpec("2", "Computing", "1", 2),
    TopicSpec("3", "Programming", "2", 3),
    TopicSpec("4", "Laptops", "2", 3),
    TopicSpec("5", "Shopping", None, 1),
    TopicSpec("6", "Coupons", "5", 2),
    TopicSpec("7", "Deals", "6", 3),
]
SOURCE_INFO = {"file": "x.tsv", "version": "1", "url": "https://example.com", "sha256": "0"}


def adaptation(**entries: object) -> Adaptation:
    return Adaptation.model_validate({"source": SOURCE_INFO, **entries})


def ids(topics: list[TopicSpec]) -> list[str]:
    return [topic.external_id for topic in topics]


def test_keeps_tiers_one_and_two_parents_first() -> None:
    assert ids(adapt(SOURCE, adaptation())) == ["1", "5", "2", "6"]


def test_prune_removes_the_whole_subtree() -> None:
    assert ids(adapt(SOURCE, adaptation(prune=[{"id": "5", "reason": "ads"}]))) == ["1", "2"]


def test_promote_moves_a_deep_topic_under_its_tier_one_ancestor() -> None:
    topics = adapt(SOURCE, adaptation(promote=[{"id": "3", "name": "Coding"}]))
    assert TopicSpec("3", "Coding", "1", 2) in topics


def test_add_creates_topics() -> None:
    topics = adapt(
        SOURCE,
        adaptation(
            add=[
                {"id": "x-history", "name": "History"},
                {"id": "x-os", "name": "OS", "parent": "1"},
            ]
        ),
    )
    assert TopicSpec("x-history", "History", None, 1) in topics
    assert TopicSpec("x-os", "OS", "1", 2) in topics


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"prune": [{"id": "99", "reason": "gone"}]}, "not in the source"),
        ({"promote": [{"id": "99"}]}, "not in the source"),
        ({"promote": [{"id": "2"}]}, "already tier 2"),
        (
            {"prune": [{"id": "5", "reason": "ads"}], "promote": [{"id": "7"}]},
            "promoted but pruned",
        ),
        (
            {
                "add": [{"id": "x-a", "name": "A", "parent": "5"}],
                "prune": [{"id": "5", "reason": "r"}],
            },
            "not a kept topic",
        ),
        ({"add": [{"id": "x-a", "name": "A"}, {"id": "x-a", "name": "B"}]}, "added twice"),
    ],
)
def test_rejects_entries_that_dont_fit_the_source(entries: dict[str, object], message: str) -> None:
    with pytest.raises(TaxonomyError, match=message):
        adapt(SOURCE, adaptation(**entries))


def test_added_ids_need_the_prefix() -> None:
    with pytest.raises(ValueError, match="pattern"):
        adaptation(add=[{"id": "history", "name": "History"}])


def test_stale_descriptions() -> None:
    topics = [
        TopicSpec("1", "Tech", None, 1),
        TopicSpec("2", "Code", "1", 2),
        TopicSpec("3", "New", None, 1),
    ]
    descriptions = {
        "1": Description(name="Tech", description="Technology."),
        "2": Description(name="Programming", description="Written before a rename."),
    }
    assert ids(stale_descriptions(topics, descriptions)) == ["2", "3"]


# The committed data files.


def test_source_file_matches_the_recorded_version() -> None:
    source = load_adaptation().source
    path = TAXONOMY_DIR / source.file
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source.sha256


def test_iab_file_reads_every_tier() -> None:
    source = load_adaptation().source
    topics = read_iab(TAXONOMY_DIR / source.file)
    assert {topic.tier for topic in topics} == {1, 2, 3, 4}
    assert TopicSpec("596", "Technology & Computing", None, 1) in topics


def test_adapted_taxonomy_is_a_two_tier_tree() -> None:
    topics = load_taxonomy()
    by_id = {topic.external_id: topic for topic in topics}
    assert len(by_id) == len(topics)
    for topic in topics:
        assert 1 <= topic.tier <= MAX_TIER
        if topic.tier == 1:
            assert topic.parent_id is None
        else:
            assert topic.parent_id is not None
            assert by_id[topic.parent_id].tier == 1


def test_adapted_taxonomy_applies_the_adaptation() -> None:
    by_id = {topic.external_id: topic for topic in load_taxonomy()}
    adaptation = load_adaptation()
    assert not {prune.id for prune in adaptation.prune} & by_id.keys()
    for promotion in adaptation.promote:
        assert by_id[promotion.id].tier == 2
    assert all(addition.id.startswith(ADDED_ID_PREFIX) for addition in adaptation.add)
    assert {addition.id for addition in adaptation.add} <= by_id.keys()


def test_every_topic_has_a_current_description() -> None:
    topics = load_taxonomy()
    descriptions = load_descriptions()
    stale = stale_descriptions(topics, descriptions)
    assert not stale, (
        f"{len(stale)} topics need descriptions: run `python -m scripts.describe_topics`"
    )
    assert descriptions.keys() == {topic.external_id for topic in topics}, (
        "descriptions.json has entries for removed topics: rerun the script to drop them"
    )
