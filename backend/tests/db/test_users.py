"""Deleting a user is a single operation that removes every row of theirs (PLAN.md §4.2)."""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.db.base import USR
from app.db.usr import (
    Event,
    Feedback,
    Pin,
    Recommendation,
    Summary,
    SurveyResponse,
    User,
    UserInterest,
    UserPpr,
    UserProfileVector,
    UserSettings,
)
from app.db.web import CrawlCycle, Document, Domain, Topic, Url
from app.enums import (
    DocumentType,
    EventKind,
    FeedbackKind,
    InterestSource,
    PinSource,
    ProfileVectorKind,
    Slice,
    Surface,
)
from app.users import delete_user

pytestmark = pytest.mark.anyio

USR_TABLES = [table for table in models.Base.metadata.sorted_tables if table.schema == USR]


async def add_web_rows(session: AsyncSession) -> tuple[Document, Topic, CrawlCycle]:
    domain = Domain(host="example.com")
    session.add(domain)
    await session.flush()
    url = Url(url="https://example.com/post", domain_id=domain.id)
    session.add(url)
    await session.flush()
    document = Document(canonical_url_id=url.id, domain_id=domain.id, type=DocumentType.ARTICLE)
    topic = Topic(external_id="596", name="Technology & Computing", tier=1)
    cycle = CrawlCycle(page_budget=10)
    session.add_all([document, topic, cycle])
    await session.flush()
    return document, topic, cycle


async def add_user_with_data(
    session: AsyncSession, email: str, web: tuple[Document, Topic, CrawlCycle]
) -> uuid.UUID:
    document, topic, cycle = web
    user = User(email=email)
    session.add(user)
    await session.flush()

    recommendation = Recommendation(
        user_id=user.id,
        document_id=document.id,
        surface=Surface.FEED,
        slice=Slice.MAIN,
        score=0.5,
        components={"ppr": 0.5},
        cycle_id=cycle.id,
    )
    session.add(recommendation)
    await session.flush()
    session.add_all(
        [
            SurveyResponse(user_id=user.id, survey_version=1, answers={"step": 1}),
            UserSettings(
                user_id=user.id,
                weights={"ppr": 1.0},
                exploration_pct=0.2,
                exploration_split={"adjacent_semantic": 0.5, "adjacent_graph": 0.5},
                content_types=[DocumentType.ARTICLE.value],
            ),
            UserInterest(
                user_id=user.id, topic_id=topic.id, weight=1.0, source=InterestSource.SURVEY
            ),
            Pin(user_id=user.id, domain_id=document.domain_id, source=PinSource.SURVEY),
            Feedback(user_id=user.id, document_id=document.id, kind=FeedbackKind.LIKE),
            Event(
                user_id=user.id,
                document_id=document.id,
                recommendation_id=recommendation.id,
                kind=EventKind.CLICK,
                surface=Surface.FEED,
                position=0,
            ),
            UserPpr(user_id=user.id, document_id=document.id, score=0.1, cycle_id=cycle.id),
            UserProfileVector(
                user_id=user.id, kind=ProfileVectorKind.INTEREST, vector=[0.1, 0.2, 0.3]
            ),
            Summary(user_id=user.id, document_id=document.id, model="fake", text="Because."),
        ]
    )
    await session.flush()
    return user.id


async def tables_with_rows(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    found = set()
    for table in USR_TABLES:
        owner = table.c.id if table.name == "users" else table.c.user_id
        if await session.scalar(sa.select(sa.exists().where(owner == user_id))):
            found.add(table.name)
    return found


async def test_delete_user_removes_all_their_data_and_nothing_else(session: AsyncSession) -> None:
    web = await add_web_rows(session)
    deleted_user = await add_user_with_data(session, "leaving@example.com", web)
    other_user = await add_user_with_data(session, "staying@example.com", web)
    every_usr_table = {table.name for table in USR_TABLES}
    # Guards the test itself: a new usr table must get a row in add_user_with_data.
    assert await tables_with_rows(session, deleted_user) == every_usr_table

    assert await delete_user(session, deleted_user)

    assert await tables_with_rows(session, deleted_user) == set()
    assert await tables_with_rows(session, other_user) == every_usr_table
    assert await session.scalar(sa.select(sa.func.count()).select_from(Document)) == 1


async def test_delete_unknown_user_returns_false(session: AsyncSession) -> None:
    assert not await delete_user(session, uuid.uuid4())
