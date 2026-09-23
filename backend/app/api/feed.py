"""The feed, search, and "why this?" (PLAN.md §6.6, §6.7, §8)."""

from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession, Embedder, Now, SettingsDep
from app.db.usr import Event, Recommendation
from app.enums import EventKind, Slice, Surface
from app.providers.openrouter import ProviderError
from app.providers.spend import SpendCapReached
from app.rank.cards import DocumentCard, load_cards
from app.rank.explain import Breakdown, ComponentScore, Evidence, Reason
from app.rank.ranker import CursorError, Page, Ranker, item_reasons

router = APIRouter(tags=["feed"])


class FeedItem(BaseModel):
    recommendation_id: int
    position: int
    """0-based position in this feed (or search) session; send it back with events."""
    slice: Slice
    score: float
    document: DocumentCard
    reasons: list[Reason]


class FeedPage(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None
    """Pass as `cursor` for the next page; null when nothing is left."""


class Why(BaseModel):
    recommendation_id: int
    surface: Surface
    query: str | None
    slice: Slice
    score: float
    created_at: datetime
    document: DocumentCard
    reasons: list[Reason]
    components: list[ComponentScore]
    """Every component's raw inputs, normalized value, weight and contribution."""
    evidence: Evidence


def feed_page(page: Page) -> FeedPage:
    return FeedPage(
        items=[
            FeedItem(
                recommendation_id=item.recommendation_id,
                position=item.position,
                slice=item.slice,
                score=item.score,
                document=item.document,
                reasons=item.reasons,
            )
            for item in page.items
        ],
        next_cursor=page.next_cursor,
    )


def _bad_cursor(error: CursorError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


@router.get("/feed")
async def feed(
    user: CurrentUser,
    session: DbSession,
    settings: SettingsDep,
    now: Now,
    cursor: str | None = None,
) -> FeedPage:
    """The next page of the user's feed. Without a cursor, a new feed session starts."""
    try:
        page = await Ranker(session, user.id, settings, now).feed(cursor)
    except CursorError as error:
        raise _bad_cursor(error) from error
    await session.commit()
    return feed_page(page)


@router.get("/search")
async def search(
    q: Annotated[str, Query(min_length=1)],
    user: CurrentUser,
    session: DbSession,
    settings: SettingsDep,
    embedder: Embedder,
    now: Now,
    cursor: str | None = None,
) -> FeedPage:
    """Documents matching the query, ranked with the user's own signals on top."""
    query = " ".join(q.split())[: settings.search_query_max_chars]
    if not query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "empty query")
    if embedder is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "search needs OPENROUTER_API_KEY")
    try:
        vector = await embedder.embed(session, query, now)
    except SpendCapReached as error:
        await session.commit()  # what earlier requests cost stays recorded
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "this month's model spend cap is reached"
        ) from error
    except ProviderError as error:
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the embedding provider failed") from error
    try:
        page = await Ranker(session, user.id, settings, now).search(query, vector, cursor)
    except CursorError as error:
        await session.commit()
        raise _bad_cursor(error) from error
    await session.commit()
    return feed_page(page)


@router.get("/recommendations/{recommendation_id}/why")
async def why(
    recommendation_id: int, user: CurrentUser, session: DbSession, settings: SettingsDep
) -> Why:
    """The full score breakdown of a served item (PLAN.md §6.7). Logs a `why_open` event."""
    recommendation = await session.scalar(
        sa.select(Recommendation).where(
            Recommendation.id == recommendation_id, Recommendation.user_id == user.id
        )
    )
    if recommendation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such recommendation")
    cards = await load_cards(session, [recommendation.document_id])
    breakdown = Breakdown.model_validate(recommendation.components)
    session.add(
        Event(
            user_id=user.id,
            document_id=recommendation.document_id,
            recommendation_id=recommendation.id,
            kind=EventKind.WHY_OPEN,
            surface=recommendation.surface,
        )
    )
    await session.commit()
    return Why(
        recommendation_id=recommendation.id,
        surface=recommendation.surface,
        query=recommendation.query,
        slice=recommendation.slice,
        score=recommendation.score,
        created_at=recommendation.created_at,
        document=cards[recommendation.document_id],
        reasons=item_reasons(breakdown, settings),
        components=breakdown.components,
        evidence=breakdown.evidence,
    )
