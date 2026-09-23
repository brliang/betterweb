"""`usr` schema: the user store (PLAN.md §4.2).

Every table is keyed by `user_id` with `ON DELETE CASCADE` from `usr.users`, so deleting a
user is one statement (`app.users.delete_user`). Tables here may reference `web`, never the
other way round.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import USR, Base, Embedding, JSONObject
from app.db.web import CrawlCycle, Document, Domain, Topic
from app.enums import (
    EventKind,
    FeedbackKind,
    InterestSource,
    PinSource,
    ProfileVectorKind,
    RankingPreset,
    Slice,
    Surface,
    Visibility,
)

NOW = sa.func.now()


def user_fk(*, primary_key: bool = False, index: bool = True) -> Mapped[uuid.UUID]:
    """The owning user. Pass index=False when a composite index already starts with user_id."""
    return mapped_column(
        sa.ForeignKey("usr.users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        index=index and not primary_key,
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        sa.CheckConstraint("email = lower(email)", name="email_lowercase"),
        {"schema": USR},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=sa.func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(unique=True)
    """Stored lowercased, so uniqueness is case-insensitive."""
    timezone: Mapped[str] = mapped_column(server_default="UTC")
    """IANA name; the nightly cycle starts at CYCLE_LOCAL_START in this zone."""
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class LoginToken(Base):
    """A one-time login link (PLAN.md §8 auth). Only the token's SHA-256 is stored."""

    __tablename__ = "login_tokens"
    __table_args__ = ({"schema": USR},)

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = user_fk()
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class UserSession(Base):
    """A signed-in browser, identified by its session cookie. Only the token's SHA-256 is
    stored."""

    __tablename__ = "sessions"
    __table_args__ = ({"schema": USR},)

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = user_fk()
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
    expires_at: Mapped[datetime]


class SurveyResponse(Base):
    """Every submission is kept; answers are never overwritten (PLAN.md §7)."""

    __tablename__ = "survey_responses"
    __table_args__ = ({"schema": USR},)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = user_fk()
    survey_version: Mapped[int]
    answers: Mapped[JSONObject]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        sa.CheckConstraint(
            "exploration_pct >= 0 AND exploration_pct <= 1", name="exploration_pct_range"
        ),
        {"schema": USR},
    )

    user_id: Mapped[uuid.UUID] = user_fk(primary_key=True)
    preset: Mapped[RankingPreset | None]
    """The survey's ranking preset; ranking uses its current RANKING_PRESETS weights. Null is
    reserved for V1's custom weights."""
    weights: Mapped[JSONObject]
    """Ranking component weights (PLAN.md §6.6): the preset's when it was chosen."""
    exploration_pct: Mapped[float]
    exploration_split: Mapped[JSONObject]
    """Shares of exploration slots per slice (semantic vs. graph)."""
    content_types: Mapped[list[str]] = mapped_column(ARRAY(sa.Text))
    """DocumentType values the user wants in their feed."""
    summaries_opt_in: Mapped[bool] = mapped_column(server_default=sa.false())
    """Off by default: opting in sends document text to a model provider (PLAN.md §6.8)."""
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW)


class UserInterest(Base):
    __tablename__ = "user_interests"
    __table_args__ = ({"schema": USR},)

    user_id: Mapped[uuid.UUID] = user_fk(primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Topic.id, ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float]
    source: Mapped[InterestSource]


class Pin(Base):
    __tablename__ = "pins"
    __table_args__ = ({"schema": USR},)

    user_id: Mapped[uuid.UUID] = user_fk(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Domain.id, ondelete="CASCADE"), primary_key=True, index=True
    )
    source: Mapped[PinSource]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class Feedback(Base):
    """Private likes, hides and domain blocks. Only ever affects this user's ranking."""

    __tablename__ = "feedback"
    __table_args__ = (
        sa.Index(None, "user_id", "document_id"),
        {"schema": USR},
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = user_fk(index=False)
    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), index=True
    )
    kind: Mapped[FeedbackKind]
    reason_text: Mapped[str | None]
    """The optional answer to "What did you like about it?"."""
    visibility: Mapped[Visibility] = mapped_column(server_default=Visibility.PRIVATE.value)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class Recommendation(Base):
    """One served item with its full score breakdown, for the "why" card (PLAN.md §6.7)."""

    __tablename__ = "recommendations"
    __table_args__ = (
        sa.Index(None, "user_id", "created_at"),
        {"schema": USR},
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = user_fk(index=False)
    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), index=True
    )
    surface: Mapped[Surface]
    query: Mapped[str | None]
    slice: Mapped[Slice]
    score: Mapped[float]
    components: Mapped[JSONObject]
    cycle_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey(CrawlCycle.id, ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class Event(Base):
    """Append-only interaction log (PLAN.md §4.2)."""

    __tablename__ = "events"
    __table_args__ = (
        sa.Index(None, "user_id", "created_at"),
        {"schema": USR},
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = user_fk(index=False)
    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), index=True
    )
    recommendation_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey(Recommendation.id, ondelete="SET NULL"), index=True
    )
    kind: Mapped[EventKind]
    surface: Mapped[Surface]
    position: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class UserPpr(Base):
    """This user's latest personalized PageRank, top-K documents only (PLAN.md §6.5)."""

    __tablename__ = "user_ppr"
    __table_args__ = (
        sa.Index(None, "user_id", "score"),
        {"schema": USR},
    )

    user_id: Mapped[uuid.UUID] = user_fk(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), primary_key=True, index=True
    )
    score: Mapped[float]
    cycle_id: Mapped[int] = mapped_column(sa.ForeignKey(CrawlCycle.id), index=True)
    """The cycle that computed this score."""


class UserProfileVector(Base):
    __tablename__ = "user_profile_vectors"
    __table_args__ = ({"schema": USR},)

    user_id: Mapped[uuid.UUID] = user_fk(primary_key=True)
    kind: Mapped[ProfileVectorKind] = mapped_column(primary_key=True)
    vector: Mapped[Embedding]
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW)


class Summary(Base):
    """Cache of opt-in LLM "why you might like this" summaries (PLAN.md §6.8)."""

    __tablename__ = "summaries"
    __table_args__ = ({"schema": USR},)

    user_id: Mapped[uuid.UUID] = user_fk(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        sa.ForeignKey(Document.id, ondelete="CASCADE"), primary_key=True, index=True
    )
    model: Mapped[str] = mapped_column(primary_key=True)
    text: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
