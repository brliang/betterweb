"""The settings page (PLAN.md §9): interests, content types, exploration share, ranking
preset and the summaries opt-in. Pins have their own routes."""

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, Now, SettingsDep
from app.api.survey import InterestChoice
from app.db.usr import UserInterest
from app.enums import DocumentType, RankingPreset
from app.preferences import PreferenceError, interest_level, replace_interests, save_settings
from app.rank.context import load_preferences
from app.score.profiles import refresh_profile_vectors
from app.settings import RankingWeights

router = APIRouter(tags=["settings"])


class UserSettingsIn(BaseModel):
    preset: RankingPreset
    exploration_pct: float
    content_types: list[DocumentType] = Field(min_length=1)
    summaries_opt_in: bool
    """Off by default: opting in sends document text and interest names to a model provider
    through OpenRouter, never account identifiers (PLAN.md §6.8)."""
    interests: list[InterestChoice]


class UserSettingsOut(BaseModel):
    preset: RankingPreset | None
    weights: RankingWeights
    """The ranking weights the preset stands for (PLAN.md §6.6)."""
    exploration_pct: float
    exploration_choices: list[float]
    exploration_split_semantic: float
    """Share of exploration slots for adjacent topics; the rest go to adjacent sites."""
    content_types: list[DocumentType]
    summaries_opt_in: bool
    interests: list[InterestChoice]


@router.get("/settings")
async def user_settings(
    user: CurrentUser, session: DbSession, settings: SettingsDep
) -> UserSettingsOut:
    """The user's settings, or the defaults before the survey."""
    preferences = await load_preferences(session, user.id, settings)
    interests = await session.execute(
        sa.select(UserInterest.topic_id, UserInterest.weight)
        .where(UserInterest.user_id == user.id)
        .order_by(UserInterest.topic_id)
    )
    return UserSettingsOut(
        preset=preferences.preset,
        weights=preferences.weights,
        exploration_pct=preferences.exploration_pct,
        exploration_choices=settings.exploration_choices,
        exploration_split_semantic=preferences.semantic_share,
        content_types=preferences.content_types,
        summaries_opt_in=preferences.summaries_opt_in,
        interests=[
            InterestChoice(topic_id=topic_id, level=interest_level(weight, settings))
            for topic_id, weight in interests
        ],
    )


@router.put("/settings")
async def update_user_settings(
    update: UserSettingsIn, user: CurrentUser, session: DbSession, settings: SettingsDep, now: Now
) -> UserSettingsOut:
    try:
        await replace_interests(
            session, user.id, [(i.topic_id, i.level) for i in update.interests], settings
        )
        await save_settings(
            session,
            user.id,
            preset=update.preset,
            exploration_pct=update.exploration_pct,
            content_types=update.content_types,
            summaries_opt_in=update.summaries_opt_in,
            settings=settings,
        )
    except PreferenceError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    await refresh_profile_vectors(session, user.id, settings, now)
    await session.commit()
    return await user_settings(user, session, settings)
