"""Dependencies shared by the routes: the database session, the signed-in user, the search
embedder. Tests override `get_session` and `get_query_embedder`."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import session_user
from app.db.usr import User
from app.search import QueryEmbedder
from app.settings import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, from the engine `create_app`'s lifespan opens."""
    async with request.app.state.sessionmaker() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_now() -> datetime:
    return datetime.now(UTC)


Now = Annotated[datetime, Depends(get_now)]


async def current_user(
    request: Request, session: DbSession, settings: SettingsDep, now: Now
) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    user = await session_user(session, token, now) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def is_admin(user: User, settings: Settings) -> bool:
    return user.email in {email.strip().lower() for email in settings.admin_emails}


async def admin_user(user: CurrentUser, settings: SettingsDep) -> User:
    if not is_admin(user, settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admins only (ADMIN_EMAILS)")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


def get_query_embedder(request: Request) -> QueryEmbedder | None:
    """None when no provider is configured (no OPENROUTER_API_KEY)."""
    embedder: QueryEmbedder | None = getattr(request.app.state, "query_embedder", None)
    return embedder


Embedder = Annotated[QueryEmbedder | None, Depends(get_query_embedder)]
