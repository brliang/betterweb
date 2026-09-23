"""Private feedback and interaction events (PLAN.md §4.2, §8). Neither ever affects another
user's results (PLAN.md §1 principle 3)."""

from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, Now, SettingsDep
from app.db.usr import Event, Feedback, Recommendation
from app.db.web import Document
from app.enums import EventKind, FeedbackKind, Surface
from app.score.profiles import refresh_profile_vectors

router = APIRouter(tags=["feedback"])

EVENT_OF_FEEDBACK = {FeedbackKind.LIKE: EventKind.LIKE, FeedbackKind.HIDE: EventKind.HIDE}


class FeedbackIn(BaseModel):
    document_id: int
    kind: FeedbackKind
    reason_text: str | None = None
    """The optional answer to "What did you like about it?" (FEEDBACK_REASON_MAX_CHARS)."""
    recommendation_id: int | None = None
    """The feed or search item it was given on, for metrics."""
    position: int | None = Field(default=None, ge=0)


class FeedbackReasonIn(BaseModel):
    reason_text: str | None


class FeedbackOut(BaseModel):
    id: int
    document_id: int
    kind: FeedbackKind
    reason_text: str | None
    created_at: datetime


class EventIn(BaseModel):
    kind: Literal[EventKind.IMPRESSION, EventKind.CLICK]
    """Likes and hides are logged by POST /feedback, "why" opens by the why route."""
    document_id: int
    recommendation_id: int | None = None
    surface: Surface
    position: int | None = Field(default=None, ge=0)


class EventsIn(BaseModel):
    events: list[EventIn] = Field(min_length=1)


class EventsRecorded(BaseModel):
    recorded: int


def _check_reason(reason_text: str | None, settings: SettingsDep) -> None:
    if reason_text is not None and len(reason_text) > settings.feedback_reason_max_chars:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"reason_text is longer than {settings.feedback_reason_max_chars} characters",
        )


def feedback_out(feedback: Feedback) -> FeedbackOut:
    return FeedbackOut(
        id=feedback.id,
        document_id=feedback.document_id,
        kind=feedback.kind,
        reason_text=feedback.reason_text,
        created_at=feedback.created_at,
    )


async def _check_references(
    session: DbSession, user: CurrentUser, pairs: list[tuple[int, int | None]]
) -> dict[int, Surface]:
    """422 unless every document exists and every recommendation is the user's and served
    that document; returns each recommendation's surface."""
    document_ids = {document_id for document_id, _ in pairs}
    known = set(await session.scalars(sa.select(Document.id).where(Document.id.in_(document_ids))))
    if missing := sorted(document_ids - known):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown documents: {missing}")
    recommendation_ids = {r for _, r in pairs if r is not None}
    served = {
        recommendation_id: (document_id, surface)
        for recommendation_id, document_id, surface in await session.execute(
            sa.select(Recommendation.id, Recommendation.document_id, Recommendation.surface).where(
                Recommendation.id.in_(recommendation_ids), Recommendation.user_id == user.id
            )
        )
    }
    for document_id, recommendation_id in pairs:
        if (
            recommendation_id is not None
            and served.get(recommendation_id, (None,))[0] != document_id
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"recommendation {recommendation_id} did not serve document {document_id}",
            )
    return {recommendation_id: surface for recommendation_id, (_, surface) in served.items()}


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
async def give_feedback(
    given: FeedbackIn, user: CurrentUser, session: DbSession, settings: SettingsDep, now: Now
) -> FeedbackOut:
    """Like, hide, or block the document's domain. Hidden documents and blocked domains leave
    the feed at once; likes and hides reshape the profile vectors right away too."""
    _check_reason(given.reason_text, settings)
    surfaces = await _check_references(
        session, user, [(given.document_id, given.recommendation_id)]
    )
    feedback = Feedback(
        user_id=user.id,
        document_id=given.document_id,
        kind=given.kind,
        reason_text=given.reason_text,
        created_at=now,
    )
    session.add(feedback)
    if (event_kind := EVENT_OF_FEEDBACK.get(given.kind)) is not None:
        session.add(
            Event(
                user_id=user.id,
                document_id=given.document_id,
                recommendation_id=given.recommendation_id,
                kind=event_kind,
                surface=surfaces.get(given.recommendation_id or 0, Surface.FEED),
                position=given.position,
                created_at=now,
            )
        )
        await session.flush()
        await refresh_profile_vectors(session, user.id, settings, now)
    await session.commit()
    return feedback_out(feedback)


@router.patch("/feedback/{feedback_id}")
async def set_feedback_reason(
    feedback_id: int,
    reason: FeedbackReasonIn,
    user: CurrentUser,
    session: DbSession,
    settings: SettingsDep,
) -> FeedbackOut:
    """Add (or clear) the optional reason after the fact: the "What did you like about it?"
    prompt comes after the like."""
    _check_reason(reason.reason_text, settings)
    feedback = await session.scalar(
        sa.select(Feedback).where(Feedback.id == feedback_id, Feedback.user_id == user.id)
    )
    if feedback is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such feedback")
    feedback.reason_text = reason.reason_text
    await session.commit()
    return feedback_out(feedback)


@router.post("/events")
async def record_events(
    batch: EventsIn, user: CurrentUser, session: DbSession, settings: SettingsDep, now: Now
) -> EventsRecorded:
    """Record a batch of impressions and clicks."""
    if len(batch.events) > settings.events_max_batch:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"at most {settings.events_max_batch} events per request",
        )
    await _check_references(
        session, user, [(event.document_id, event.recommendation_id) for event in batch.events]
    )
    session.add_all(
        Event(
            user_id=user.id,
            document_id=event.document_id,
            recommendation_id=event.recommendation_id,
            kind=event.kind,
            surface=event.surface,
            position=event.position,
            created_at=now,
        )
        for event in batch.events
    )
    await session.commit()
    return EventsRecorded(recorded=len(batch.events))
