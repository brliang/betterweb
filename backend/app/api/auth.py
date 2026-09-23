"""Signing in and out (PLAN.md §8 "Auth"), and who is signed in."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Body, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession, Now, SettingsDep, is_admin
from app.auth import end_session, redeem_login_token, start_session
from app.db.usr import SurveyResponse, User
from app.settings import Settings

router = APIRouter(tags=["auth"])


class Me(BaseModel):
    email: str
    timezone: str
    survey_completed: bool
    is_admin: bool


class LoginRequest(BaseModel):
    token: str
    """From the login link (`python -m app.worker users login-link EMAIL`)."""


async def me_of(session: DbSession, user: User, settings: Settings) -> Me:
    surveyed = await session.scalar(sa.select(sa.exists().where(SurveyResponse.user_id == user.id)))
    return Me(
        email=user.email,
        timezone=user.timezone,
        survey_completed=bool(surveyed),
        is_admin=is_admin(user, settings),
    )


@router.post("/auth/session")
async def sign_in(
    login: Annotated[LoginRequest, Body()],
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    now: Now,
) -> Me:
    """Trade a one-time login token for a session cookie."""
    user_id = await redeem_login_token(session, login.token, now)
    user = await session.get(User, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired login link")
    token, expires_at = await start_session(session, user.id, settings, now)
    me = await me_of(session, user, settings)
    await session.commit()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        expires=expires_at,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return me


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(request: Request, session: DbSession, settings: SettingsDep) -> Response:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await end_session(session, token)
        await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return response


@router.get("/me")
async def me(user: CurrentUser, session: DbSession, settings: SettingsDep) -> Me:
    return await me_of(session, user, settings)
