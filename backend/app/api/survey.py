"""The signup survey (PLAN.md §7): stored as submitted, then materialized into interests,
pins and settings."""

from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUser, DbSession, Now, SettingsDep
from app.db.usr import SurveyResponse
from app.enums import DocumentType, InterestLevel, RankingPreset
from app.preferences import (
    PreferenceError,
    parse_site,
    replace_interests,
    replace_pins,
    save_settings,
)
from app.score.profiles import refresh_profile_vectors

router = APIRouter(tags=["survey"])


class InterestChoice(BaseModel):
    topic_id: int
    level: InterestLevel


class SurveyAnswers(BaseModel):
    """Version 1 of the survey, one field per step."""

    model_config = ConfigDict(extra="forbid")

    interests: list[InterestChoice] = Field(min_length=1)
    sites: list[str] = []
    """Domains or URLs to pin, typed in or picked from the suggested sources."""
    content_types: list[DocumentType] = Field(min_length=1)
    exploration_pct: float
    """One of the offered shares (GET /settings `exploration_choices`)."""
    preset: RankingPreset


class SurveySubmission(BaseModel):
    version: Literal[1] = 1
    answers: SurveyAnswers


class SurveyOut(BaseModel):
    id: int
    version: int
    answers: SurveyAnswers
    created_at: datetime


def survey_out(response: SurveyResponse) -> SurveyOut:
    return SurveyOut(
        id=response.id,
        version=response.survey_version,
        answers=SurveyAnswers.model_validate(response.answers),
        created_at=response.created_at,
    )


@router.post("/survey", status_code=status.HTTP_201_CREATED)
async def submit_survey(
    submission: SurveySubmission,
    user: CurrentUser,
    session: DbSession,
    settings: SettingsDep,
    now: Now,
) -> SurveyOut:
    """Store the answers and apply them: they replace the user's interests, pins and
    settings (summaries stay as they were: off unless opted in on the settings page)."""
    answers = submission.answers
    try:
        sites = [parse_site(site, settings) for site in answers.sites]
        await replace_interests(
            session, user.id, [(i.topic_id, i.level) for i in answers.interests], settings
        )
        await save_settings(
            session,
            user.id,
            preset=answers.preset,
            exploration_pct=answers.exploration_pct,
            content_types=answers.content_types,
            summaries_opt_in=None,
            settings=settings,
        )
    except PreferenceError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    await replace_pins(session, user.id, sites, settings)
    await refresh_profile_vectors(session, user.id, settings, now)
    response = SurveyResponse(
        user_id=user.id,
        survey_version=submission.version,
        answers=answers.model_dump(mode="json"),
        created_at=now,
    )
    session.add(response)
    await session.commit()
    return survey_out(response)


@router.get("/survey/latest")
async def latest_survey(user: CurrentUser, session: DbSession) -> SurveyOut:
    response = await session.scalar(
        sa.select(SurveyResponse)
        .where(SurveyResponse.user_id == user.id)
        .order_by(SurveyResponse.created_at.desc(), SurveyResponse.id.desc())
        .limit(1)
    )
    if response is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no survey taken yet")
    return survey_out(response)
