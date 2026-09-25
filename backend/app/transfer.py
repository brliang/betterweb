"""Move a user's preferences between deployments (`users export` / `users import`).

Only what the user chose travels: their account, settings, interests, pinned sites and survey
answers. Likes, hides, recommendations and events point at documents of the old deployment's
crawl, so they stay behind; pinning the sites enqueues them as seeds of the new crawl.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ensure_user
from app.db.base import JSONObject
from app.db.usr import Pin, SurveyResponse, User, UserInterest, UserSettings
from app.db.web import Domain, Topic
from app.enums import InterestSource, PinSource, RankingPreset
from app.preferences import parse_site, pin_site
from app.score.profiles import refresh_profile_vectors
from app.settings import Settings

FORMAT_VERSION = 1


class TransferError(Exception):
    """The user or something they refer to doesn't exist."""


class SettingsOut(BaseModel):
    preset: RankingPreset | None
    weights: JSONObject
    exploration_pct: float
    exploration_split: JSONObject
    content_types: list[str]
    summaries_opt_in: bool


class InterestOut(BaseModel):
    topic: str
    """The topic's external (IAB) ID: database IDs differ between deployments."""
    weight: float
    source: InterestSource


class PinOut(BaseModel):
    host: str
    source: PinSource


class SurveyOut(BaseModel):
    survey_version: int
    answers: JSONObject
    created_at: datetime


class UserExport(BaseModel):
    format_version: int = FORMAT_VERSION
    email: str
    timezone: str
    settings: SettingsOut | None
    interests: list[InterestOut]
    pins: list[PinOut]
    surveys: list[SurveyOut]


async def export_user(session: AsyncSession, email: str) -> UserExport:
    user = await session.scalar(sa.select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise TransferError(f"no user {email!r}")
    settings = await session.get(UserSettings, user.id)
    interests = await session.execute(
        sa.select(Topic.external_id, UserInterest.weight, UserInterest.source)
        .join(Topic, Topic.id == UserInterest.topic_id)
        .where(UserInterest.user_id == user.id)
        .order_by(Topic.external_id)
    )
    pins = await session.execute(
        sa.select(Domain.host, Pin.source)
        .join(Domain, Domain.id == Pin.domain_id)
        .where(Pin.user_id == user.id)
        .order_by(Pin.created_at, Domain.host)
    )
    surveys = await session.scalars(
        sa.select(SurveyResponse)
        .where(SurveyResponse.user_id == user.id)
        .order_by(SurveyResponse.created_at, SurveyResponse.id)
    )
    return UserExport(
        email=user.email,
        timezone=user.timezone,
        settings=SettingsOut.model_validate(settings, from_attributes=True) if settings else None,
        interests=[InterestOut(topic=t, weight=w, source=s) for t, w, s in interests],
        pins=[PinOut(host=host, source=source) for host, source in pins],
        surveys=[SurveyOut.model_validate(s, from_attributes=True) for s in surveys],
    )


async def import_user(session: AsyncSession, data: UserExport, settings: Settings) -> None:
    """Create the user (or update them) with the exported preferences, replacing their
    settings, interests and pins. Commits nothing; the caller does."""
    if data.format_version != FORMAT_VERSION:
        raise TransferError(f"unsupported export format {data.format_version}")
    user_id = await ensure_user(session, data.email)
    await session.execute(sa.update(User).where(User.id == user_id).values(timezone=data.timezone))

    if data.settings is not None:
        values = data.settings.model_dump()
        await session.execute(
            pg_insert(UserSettings)
            .values(user_id=user_id, **values)
            .on_conflict_do_update(
                index_elements=[UserSettings.user_id], set_={**values, "updated_at": sa.func.now()}
            )
        )

    rows = await session.execute(
        sa.select(Topic.external_id, Topic.id).where(
            Topic.external_id.in_([i.topic for i in data.interests])
        )
    )
    topics = dict(rows.tuples().all())
    if missing := sorted({i.topic for i in data.interests} - topics.keys()):
        raise TransferError(f"topics not in this deployment's taxonomy: {missing}")
    await session.execute(sa.delete(UserInterest).where(UserInterest.user_id == user_id))
    session.add_all(
        UserInterest(user_id=user_id, topic_id=topics[i.topic], weight=i.weight, source=i.source)
        for i in data.interests
    )

    kept = []
    for pin in data.pins:
        domain_id = await pin_site(session, user_id, parse_site(pin.host, settings), settings)
        await session.execute(
            sa.update(Pin)
            .where(Pin.user_id == user_id, Pin.domain_id == domain_id)
            .values(source=pin.source)
        )
        kept.append(domain_id)
    await session.execute(sa.delete(Pin).where(Pin.user_id == user_id, Pin.domain_id.not_in(kept)))

    known = set(
        await session.scalars(
            sa.select(SurveyResponse.created_at).where(SurveyResponse.user_id == user_id)
        )
    )
    session.add_all(
        SurveyResponse(user_id=user_id, **survey.model_dump())
        for survey in data.surveys
        if survey.created_at not in known  # importing twice adds no duplicates
    )
    await session.flush()
    await refresh_profile_vectors(session, user_id, settings, datetime.now(UTC))
