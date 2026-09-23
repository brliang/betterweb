"""Score breakdowns and "why this?" reasons (PLAN.md §6.7).

Every served item stores a `Breakdown` in usr.recommendations.components: each component's
raw inputs, normalized value, weight and contribution, plus the evidence behind them (which
trusted sites link to it, which interests it matches, the nearest liked document). Reasons are
derived from the stored breakdown alone, so the feed and GET /recommendations/{id}/why always
agree.
"""

from enum import StrEnum

from pydantic import BaseModel

from app.enums import Slice
from app.rank.scoring import Component

BREAKDOWN_VERSION = 1


class TopicRef(BaseModel):
    id: int
    name: str


class LikedRef(BaseModel):
    document_id: int
    title: str | None
    similarity: float


class Exploration(BaseModel):
    """Why an exploration slice picked the item."""

    slice: Slice
    adjacent_topic: TopicRef | None = None
    """Semantic, topic route: the parent or sibling topic of an interest it is tagged with..."""
    interest_topic: TopicRef | None = None
    """...and that interest."""
    outside_topic: TopicRef | None = None
    """Graph: its top topic, outside the user's interests."""
    path: list[str] = []
    """Graph: the domains linking the way from a pinned domain (first) to its domain."""


class Evidence(BaseModel):
    pinned_domain: str | None = None
    """Its domain, if the user pinned it."""
    trusted_linkers: list[str] = []
    """Pinned domains linking to it (examples)..."""
    trusted_linker_count: int = 0
    """...and how many there are."""
    other_linkers: list[str] = []
    """Other domains linking to it, most PageRank first (examples)..."""
    other_linker_count: int = 0
    interest_topics: list[TopicRef] = []
    """The user's interests its topic tags fall under, strongest first."""
    nearest_liked: LikedRef | None = None
    age_days: float
    published: bool
    """Age counts from the publication date if known, else from when it was first crawled."""
    exploration: Exploration | None = None


class ComponentScore(BaseModel):
    name: Component
    inputs: dict[str, float]
    """Raw numbers before normalization (cosines, PageRank, age)."""
    value: float
    """Normalized to [0, 1] (PLAN.md §6.6)."""
    weight: float
    contribution: float
    """weight x value; negative for the hide penalty. The components sum to the score."""


class Breakdown(BaseModel):
    version: int = BREAKDOWN_VERSION
    components: list[ComponentScore]
    evidence: Evidence


class ReasonKind(StrEnum):
    EXPLORATION = "exploration"
    SOURCES = "sources"
    INTEREST = "interest"
    LIKED = "liked"
    RECENCY = "recency"
    SEARCH = "search"


class Reason(BaseModel):
    kind: ReasonKind
    text: str


def _examples(hosts: list[str], limit: int) -> str:
    return ", ".join(hosts[:limit])


def _sources(evidence: Evidence, examples: int) -> str:
    if evidence.pinned_domain is not None:
        return f"From {evidence.pinned_domain}, a site you pinned"
    count = evidence.trusted_linker_count
    if count == 1 and evidence.trusted_linkers:
        return f"Linked from {evidence.trusted_linkers[0]}, a site you trust"
    if count > 1:
        return (
            f"Linked from {count} sites you trust "
            f"(e.g. {_examples(evidence.trusted_linkers, examples)})"
        )
    if evidence.other_linkers:
        return (
            "Linked from sites your sources link to "
            f"(e.g. {_examples(evidence.other_linkers, examples)})"
        )
    return "Close to your sources in the link graph"


def _interest(evidence: Evidence, examples: int) -> str:
    names = [topic.name for topic in evidence.interest_topics[:examples]]
    if not names:
        return "Close to your interests"
    label = "interest" if len(names) == 1 else "interests"
    return f"Matches your {label}: {', '.join(names)}"


def _liked(evidence: Evidence) -> str:
    liked = evidence.nearest_liked
    if liked is None or not liked.title:
        return "Similar to things you liked"
    return f"Similar to “{liked.title}”, which you liked"


def _recency(evidence: Evidence) -> str:
    days = int(evidence.age_days)
    verb = "Published" if evidence.published else "Found"
    if days < 1:
        return f"{verb} today"
    if days == 1:
        return f"{verb} yesterday"
    return f"{verb} {days} days ago"


def _exploration(exploration: Exploration) -> str:
    if exploration.slice == Slice.ADJACENT_GRAPH:
        topic = exploration.outside_topic.name if exploration.outside_topic else "a new topic"
        if not exploration.path:
            return f"Exploring: {topic}, near sites you trust"
        if len(exploration.path) == 1:
            return f"Exploring: {topic}, from a site {exploration.path[0]} links to"
        return f"Exploring: {topic}, {len(exploration.path)} links away from {exploration.path[0]}"
    if exploration.adjacent_topic is not None and exploration.interest_topic is not None:
        return (
            f"Exploring: {exploration.adjacent_topic.name}, "
            f"next to your interest {exploration.interest_topic.name}"
        )
    return "Exploring: near your interests, past the closest matches"


def reasons(
    breakdown: Breakdown, *, max_reasons: int, min_contribution: float, examples: int
) -> list[Reason]:
    """The top contributing components in plain language, after the exploration reason if
    any. Penalties and components contributing less than `min_contribution` give none."""
    evidence = breakdown.evidence
    found: list[Reason] = []
    if evidence.exploration is not None:
        found.append(Reason(kind=ReasonKind.EXPLORATION, text=_exploration(evidence.exploration)))
    ranked = sorted(breakdown.components, key=lambda component: -component.contribution)
    for component in ranked:
        if len(found) >= max_reasons or component.contribution < min_contribution:
            break
        match component.name:
            case Component.PPR:
                found.append(Reason(kind=ReasonKind.SOURCES, text=_sources(evidence, examples)))
            case Component.INTEREST:
                found.append(Reason(kind=ReasonKind.INTEREST, text=_interest(evidence, examples)))
            case Component.FEEDBACK:
                found.append(Reason(kind=ReasonKind.LIKED, text=_liked(evidence)))
            case Component.RECENCY:
                found.append(Reason(kind=ReasonKind.RECENCY, text=_recency(evidence)))
            case Component.QUERY:
                found.append(Reason(kind=ReasonKind.SEARCH, text="Matches your search"))
            case Component.HIDE:
                pass
    return found[:max_reasons]
