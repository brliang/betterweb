"""Login users for the database roles (`db logins`, app.db.logins)."""

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.logins import LoginError, ensure_logins

pytestmark = pytest.mark.anyio


async def logins(session: AsyncSession) -> dict[str, set[str]]:
    """Login users named *_login, with the discovery_* roles each belongs to."""
    rows = await session.execute(
        sa.text(
            "SELECT member.rolname, grp.rolname FROM pg_roles member "
            "JOIN pg_auth_members m ON m.member = member.oid "
            "JOIN pg_roles grp ON grp.oid = m.roleid "
            "WHERE member.rolname LIKE 'discovery_%_login' AND member.rolcanlogin"
        )
    )
    found: dict[str, set[str]] = {}
    for member, group in rows:
        found.setdefault(member, set()).add(group)
    return found


async def test_each_configured_role_gets_a_login_user(session: AsyncSession) -> None:
    # CREATE ROLE is transactional, so the test's rollback removes these again.
    conn = await session.connection()
    passwords = {"discovery_api": SecretStr("a'b"), "discovery_score": SecretStr("s")}
    assert await ensure_logins(conn, passwords) == [
        "discovery_score_login",
        "discovery_api_login",
    ]
    assert await logins(session) == {
        "discovery_score_login": {"discovery_score"},
        "discovery_api_login": {"discovery_api"},
    }
    # Running again (a new password) updates them in place.
    await ensure_logins(conn, {"discovery_api": SecretStr("new")})
    assert set(await logins(session)) == {"discovery_score_login", "discovery_api_login"}


async def test_only_the_group_roles_get_login_users(session: AsyncSession) -> None:
    conn = await session.connection()
    with pytest.raises(LoginError, match="postgres"):
        await ensure_logins(conn, {"postgres": SecretStr("x")})
