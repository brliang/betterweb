"""Everything about a user that ranking needs (PLAN.md §6.6), and the hard filters.

Private signals (likes, hides, blocks, events) are read for this user only and only ever
shape this user's ranking (PLAN.md §1 principle 3).
"""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.base import Embedding
from app.db.usr import Event, Feedback, UserInterest, UserProfileVector, UserSettings
from app.db.web import Document, GlobalScore, Topic
from app.enums import (
    DocumentType,
    EventKind,
    FeedbackKind,
    ProfileVectorKind,
    RankingPreset,
    Slice,
)
from app.pins import pinned_domains
from app.rank.topics import Interests, TopicTree
from app.settings import RankingWeights, Settings


@dataclass(frozen=True)
class Preferences:
    """A user's ranking settings, or the defaults before the survey."""

    preset: RankingPreset | None
    weights: RankingWeights
    exploration_pct: float
    exploration_split: dict[Slice, float]
    content_types: list[DocumentType]
    summaries_opt_in: bool

    @property
    def semantic_share(self) -> float:
        semantic = self.exploration_split.get(Slice.ADJACENT_SEMANTIC, 0.0)
        graph = self.exploration_split.get(Slice.ADJACENT_GRAPH, 0.0)
        return semantic / (semantic + graph) if semantic + graph > 0 else 0.5


def default_split(settings: Settings) -> dict[Slice, float]:
    return {
        Slice.ADJACENT_SEMANTIC: settings.exploration_split_semantic,
        Slice.ADJACENT_GRAPH: 1 - settings.exploration_split_semantic,
    }


def preferences_of(row: UserSettings | None, settings: Settings) -> Preferences:
    if row is None:
        return Preferences(
            preset=settings.default_preset,
            weights=settings.ranking_presets[settings.default_preset],
            exploration_pct=settings.exploration_pct,
            exploration_split=default_split(settings),
            content_types=list(settings.default_content_types),
            summaries_opt_in=False,
        )
    weights = (
        settings.ranking_presets[row.preset]
        if row.preset is not None
        else RankingWeights.model_validate(row.weights)
    )
    split = {
        Slice(name): float(share) if isinstance(share, int | float) else 0.0
        for name, share in row.exploration_split.items()
    }
    return Preferences(
        preset=row.preset,
        weights=weights,
        exploration_pct=row.exploration_pct,
        exploration_split=split,
        content_types=[DocumentType(kind) for kind in row.content_types],
        summaries_opt_in=row.summaries_opt_in,
    )


async def load_preferences(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings
) -> Preferences:
    return preferences_of(await session.get(UserSettings, user_id), settings)


async def load_topic_tree(session: AsyncSession) -> TopicTree:
    rows = (await session.execute(sa.select(Topic.id, Topic.parent_id, Topic.name))).all()
    return TopicTree(
        parents={topic: parent for topic, parent, _ in rows},
        names={topic: name for topic, _, name in rows},
    )


@dataclass(frozen=True)
class UserContext:
    user_id: uuid.UUID
    preferences: Preferences
    interests: Interests
    pinned: dict[int, str]
    """Domain ID -> host of every domain the user's pins cover."""
    vectors: dict[ProfileVectorKind, Embedding]
    liked: list[int]
    hidden: list[int]
    """The most recent PROFILE_MAX_DOCUMENTS documents whose latest feedback is a like (or a
    hide)."""
    documents: int
    """Documents in the corpus, the scale of PageRank (which sums to 1)."""
    cycle_id: int | None
    """The crawl cycle whose scores ranking uses."""


async def _latest_feedback(
    session: AsyncSession, user_id: uuid.UUID, kind: FeedbackKind, limit: int
) -> list[int]:
    latest = (
        sa.select(Feedback.document_id, Feedback.kind, Feedback.created_at)
        .distinct(Feedback.document_id)
        .where(
            Feedback.user_id == user_id,
            Feedback.kind.in_([FeedbackKind.LIKE, FeedbackKind.HIDE]),
        )
        .order_by(Feedback.document_id, Feedback.created_at.desc(), Feedback.id.desc())
        .subquery()
    )
    rows = await session.scalars(
        sa.select(latest.c.document_id)
        .where(latest.c.kind == kind)
        .order_by(latest.c.created_at.desc(), latest.c.document_id)
        .limit(limit)
    )
    return list(rows)


async def load_context(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings
) -> UserContext:
    tree = await load_topic_tree(session)
    weights = dict(
        (
            await session.execute(
                sa.select(UserInterest.topic_id, UserInterest.weight).where(
                    UserInterest.user_id == user_id
                )
            )
        )
        .tuples()
        .all()
    )
    pinned = {
        domain_id: host for _, domain_id, host in await session.execute(pinned_domains(user_id))
    }
    vectors = {
        kind: [float(x) for x in vector]
        for kind, vector in await session.execute(
            sa.select(UserProfileVector.kind, UserProfileVector.vector).where(
                UserProfileVector.user_id == user_id
            )
        )
    }
    limit = settings.profile_max_documents
    return UserContext(
        user_id=user_id,
        preferences=await load_preferences(session, user_id, settings),
        interests=Interests(tree, weights),
        pinned=pinned,
        vectors=vectors,
        liked=await _latest_feedback(session, user_id, FeedbackKind.LIKE, limit),
        hidden=await _latest_feedback(session, user_id, FeedbackKind.HIDE, limit),
        documents=await session.scalar(sa.select(sa.func.count()).select_from(Document)) or 0,
        cycle_id=await session.scalar(sa.select(sa.func.max(GlobalScore.cycle_id))),
    )


def hard_filters(
    context: UserContext, settings: Settings, *, seen: bool
) -> list[sa.ColumnElement[bool]]:
    """Conditions on `Document` every candidate must meet (PLAN.md §6.6 "Hard filters").

    Always: its type is one the user wants, the user hasn't hidden it, and they haven't
    blocked its domain. With `seen` (the feed, not search, where finding something again is
    the point): they haven't liked or clicked it either, and it hasn't been shown more than
    IMPRESSION_MAX_UNCLICKED times.
    """
    user_id = context.user_id
    feedback_document = aliased(Document)
    blocked = (
        sa.select(feedback_document.domain_id)
        .join(Feedback, Feedback.document_id == feedback_document.id)
        .where(Feedback.user_id == user_id, Feedback.kind == FeedbackKind.BLOCK_DOMAIN)
    )
    conditions = [
        Document.type.in_(context.preferences.content_types),
        ~sa.exists().where(
            Feedback.user_id == user_id,
            Feedback.document_id == Document.id,
            Feedback.kind.in_(
                [FeedbackKind.LIKE, FeedbackKind.HIDE] if seen else [FeedbackKind.HIDE]
            ),
        ),
        Document.domain_id.not_in(blocked),
    ]
    if seen:
        shown = (
            sa.select(Event.document_id)
            .where(Event.user_id == user_id, Event.kind == EventKind.IMPRESSION)
            .group_by(Event.document_id)
            .having(
                sa.func.count(sa.distinct(sa.func.coalesce(Event.recommendation_id, -Event.id)))
                > settings.impression_max_unclicked
            )
        )
        conditions += [
            ~sa.exists().where(
                Event.user_id == user_id,
                Event.document_id == Document.id,
                Event.kind == EventKind.CLICK,
            ),
            Document.id.not_in(shown),
        ]
    return conditions
