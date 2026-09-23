"""Closed sets of values shared by the database models and the API (PLAN.md §4).

Each is stored as text with a CHECK constraint. Adding a value needs a migration that
recreates that constraint; values marked "reserved" in the plan are added in V1, not now.
"""

from enum import StrEnum


class DomainStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    """The site refuses the crawler (robots.txt disallows it, 403, bot challenge). Never evade."""
    UNREACHABLE = "unreachable"


class DocumentType(StrEnum):
    ARTICLE = "article"
    POST = "post"
    THREAD = "thread"
    PAPER = "paper"
    PDF = "pdf"
    VIDEO = "video"
    PAGE = "page"


class FrontierReason(StrEnum):
    NEW = "new"
    RECRAWL = "recrawl"


class CycleStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SpendPurpose(StrEnum):
    """What a model provider was paid for (web.provider_spend)."""

    EMBED_DOCUMENTS = "embed_documents"
    EMBED_TOPICS = "embed_topics"
    SEARCH = "search"
    """Embedding a search query (GET /search)."""
    SUMMARY = "summary"
    """An opt-in "Why might I like this?" summary (POST /documents/{id}/summary)."""


class InterestSource(StrEnum):
    SURVEY = "survey"
    LEARNED = "learned"


class InterestLevel(StrEnum):
    """How strongly a user picked an interest in the survey (PLAN.md §7 step 1); stored as the
    weight INTEREST_LEVEL_WEIGHTS gives it."""

    INTERESTED = "interested"
    VERY_INTERESTED = "very_interested"


class PinSource(StrEnum):
    SURVEY = "survey"
    SUGGESTED = "suggested"
    # Reserved for V1: MANUAL = "manual"


class FeedbackKind(StrEnum):
    LIKE = "like"
    HIDE = "hide"
    BLOCK_DOMAIN = "block_domain"


class Visibility(StrEnum):
    PRIVATE = "private"
    # Reserved for V1: PUBLIC = "public"


class EventKind(StrEnum):
    IMPRESSION = "impression"
    CLICK = "click"
    LIKE = "like"
    HIDE = "hide"
    WHY_OPEN = "why_open"
    SUMMARY_VIEW = "summary_view"


class Surface(StrEnum):
    FEED = "feed"
    SEARCH = "search"


class Slice(StrEnum):
    MAIN = "main"
    ADJACENT_SEMANTIC = "adjacent_semantic"
    ADJACENT_GRAPH = "adjacent_graph"


class ProfileVectorKind(StrEnum):
    INTEREST = "interest"
    LIKED = "liked"
    HIDDEN = "hidden"


class RankingPreset(StrEnum):
    """Survey step 5 (PLAN.md §7); RANKING_PRESETS maps each to ranking weights."""

    BALANCED = "balanced"
    TRUST_MY_SOURCES = "trust_my_sources"
    MATCH_MY_INTERESTS = "match_my_interests"
    FRESH = "fresh"
