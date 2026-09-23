"""Minimal V0 auth (PLAN.md §8): one-time login links, then a session cookie.

`python -m app.worker users login-link EMAIL` makes a link to the frontend's /login page; the
page trades its token for a session (POST /auth/session). Only SHA-256 hashes of tokens are
stored, so a leaked database holds no working token. Everything is scoped by user_id, so
multi-user auth in V1 replaces only how users sign in.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.usr import LoginToken, User, UserSession
from app.settings import Settings

TOKEN_BYTES = 32


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def ensure_user(session: AsyncSession, email: str) -> uuid.UUID:
    email = email.strip().lower()
    # DO UPDATE (a no-op) rather than DO NOTHING, so RETURNING gives an existing user's ID too.
    result = await session.execute(
        pg_insert(User)
        .values(email=email)
        .on_conflict_do_update(index_elements=[User.email], set_={"email": email})
        .returning(User.id)
    )
    return result.scalar_one()


async def create_login_link(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings, now: datetime
) -> str:
    token = new_token()
    session.add(
        LoginToken(
            token_hash=token_hash(token),
            user_id=user_id,
            expires_at=now + timedelta(minutes=settings.login_token_ttl_minutes),
        )
    )
    await session.flush()
    return f"{settings.app_base_url.rstrip('/')}/login?{urlencode({'token': token})}"


async def redeem_login_token(session: AsyncSession, token: str, now: datetime) -> uuid.UUID | None:
    """The token's user, marking the token used; None if it is unknown, used or expired."""
    return await session.scalar(
        sa.update(LoginToken)
        .where(
            LoginToken.token_hash == token_hash(token),
            LoginToken.used_at.is_(None),
            LoginToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(LoginToken.user_id)
    )


async def start_session(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings, now: datetime
) -> tuple[str, datetime]:
    """A new session token and when it expires."""
    token = new_token()
    expires_at = now + timedelta(days=settings.session_ttl_days)
    session.add(UserSession(token_hash=token_hash(token), user_id=user_id, expires_at=expires_at))
    await session.flush()
    return token, expires_at


async def session_user(session: AsyncSession, token: str, now: datetime) -> User | None:
    user: User | None = await session.scalar(
        sa.select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == token_hash(token), UserSession.expires_at > now)
    )
    return user


async def end_session(session: AsyncSession, token: str) -> None:
    await session.execute(sa.delete(UserSession).where(UserSession.token_hash == token_hash(token)))
