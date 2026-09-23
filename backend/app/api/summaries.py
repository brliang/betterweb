"""Opt-in "Why might I like this?" summaries (PLAN.md §6.8, §8)."""

from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession, Now, SummarizerDep
from app.db.usr import Event, Recommendation, UserSettings
from app.enums import EventKind
from app.providers.openrouter import ProviderError
from app.providers.spend import SpendCapReached

router = APIRouter(tags=["summaries"])


class SummaryIn(BaseModel):
    recommendation_id: int
    """The feed or search item it is asked for on; logged with the `summary_view` event."""


class SummaryOut(BaseModel):
    document_id: int
    text: str
    model: str
    """The model that wrote it."""
    created_at: datetime


@router.post("/documents/{document_id}/summary")
async def summarize(
    document_id: int,
    request: SummaryIn,
    user: CurrentUser,
    session: DbSession,
    summarizer: SummarizerDep,
    now: Now,
) -> SummaryOut:
    """A short AI-written note on why the user might like the document: cached, else written
    now. Only for users who opted in (403 otherwise). Logs a `summary_view` event."""
    recommendation = await session.scalar(
        sa.select(Recommendation).where(
            Recommendation.id == request.recommendation_id,
            Recommendation.user_id == user.id,
            Recommendation.document_id == document_id,
        )
    )
    if recommendation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such recommendation of this document")
    opted_in = await session.scalar(
        sa.select(UserSettings.summaries_opt_in).where(UserSettings.user_id == user.id)
    )
    if not opted_in:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "summaries are off; turn them on in your settings"
        )
    if summarizer is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "summaries need OPENROUTER_API_KEY"
        )
    try:
        summary = await summarizer.summary(session, user.id, document_id, now)
    except SpendCapReached as error:
        await session.commit()  # what earlier requests cost stays recorded
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "this month's model spend cap is reached"
        ) from error
    except ProviderError as error:
        await session.commit()  # a failed reply may still have been paid for
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the model provider failed") from error
    if summary is None:  # the document was deleted after it was served
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such document")
    session.add(
        Event(
            user_id=user.id,
            document_id=document_id,
            recommendation_id=recommendation.id,
            kind=EventKind.SUMMARY_VIEW,
            surface=recommendation.surface,
        )
    )
    out = SummaryOut(
        document_id=summary.document_id,
        text=summary.text,
        model=summary.model,
        created_at=summary.created_at,
    )
    await session.commit()
    return out
