"""Moving a user's preferences to another deployment (`users export` / `users import`)."""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.usr import Feedback, SurveyResponse, UserInterest, UserSettings
from app.db.web import FrontierEntry, Url
from app.enums import FeedbackKind, InterestSource, PinSource, RankingPreset
from app.settings import Settings
from app.transfer import TransferError, export_user, import_user
from tests.db.builders import add_document, add_topic, add_user, pin

pytestmark = pytest.mark.anyio

SETTINGS = Settings(_env_file=None)
TAKEN = datetime(2026, 9, 23, 12, tzinfo=UTC)


async def a_user_with_preferences(session: AsyncSession) -> None:
    user_id = await add_user(session, "me@example.com")
    session.add(
        UserSettings(
            user_id=user_id,
            preset=RankingPreset.FRESH,
            weights=SETTINGS.ranking_presets[RankingPreset.FRESH].model_dump(),
            exploration_pct=0.35,
            exploration_split={"semantic": 0.5, "graph": 0.5},
            content_types=["article"],
            summaries_opt_in=True,
        )
    )
    topic = await add_topic(session, "Science", external_id="464")
    session.add(
        UserInterest(user_id=user_id, topic_id=topic, weight=2.0, source=InterestSource.SURVEY)
    )
    await pin(session, user_id, "blog.example")
    session.add(
        SurveyResponse(user_id=user_id, survey_version=1, answers={"a": 1}, created_at=TAKEN)
    )
    document = await add_document(session, "https://blog.example/post")
    session.add(Feedback(user_id=user_id, document_id=document, kind=FeedbackKind.LIKE))
    await session.flush()


async def test_preferences_survive_the_round_trip(session: AsyncSession) -> None:
    await a_user_with_preferences(session)
    exported = await export_user(session, "Me@Example.com")
    assert exported.settings is not None
    assert (exported.settings.preset, exported.settings.summaries_opt_in) == (
        RankingPreset.FRESH,
        True,
    )
    assert [(i.topic, i.weight) for i in exported.interests] == [("464", 2.0)]
    assert [(p.host, p.source) for p in exported.pins] == [("blog.example", PinSource.SURVEY)]

    moved = exported.model_copy(update={"email": "moved@example.com"})
    await import_user(session, moved, SETTINGS)
    await import_user(session, moved, SETTINGS)  # a second import changes nothing
    again = await export_user(session, "moved@example.com")
    assert again == moved
    # Likes stay behind: they point at the old deployment's documents.
    assert await session.scalar(sa.select(sa.func.count()).select_from(Feedback)) == 1


async def test_imported_pins_seed_the_crawl(session: AsyncSession) -> None:
    await a_user_with_preferences(session)
    exported = await export_user(session, "me@example.com")
    moved = exported.model_copy(update={"email": "moved@example.com"})
    await session.execute(sa.delete(FrontierEntry))
    await import_user(session, moved, SETTINGS)
    seeds = await session.scalars(
        sa.select(Url.url).join(FrontierEntry, FrontierEntry.url_id == Url.id)
    )
    assert list(seeds) == ["https://blog.example/"]


async def test_unknown_topics_are_refused(session: AsyncSession) -> None:
    await a_user_with_preferences(session)
    exported = await export_user(session, "me@example.com")
    exported.interests[0].topic = "no-such-topic"
    with pytest.raises(TransferError, match="no-such-topic"):
        await import_user(session, exported, SETTINGS)


async def test_exporting_an_unknown_user_is_an_error(session: AsyncSession) -> None:
    with pytest.raises(TransferError):
        await export_user(session, "nobody@example.com")
