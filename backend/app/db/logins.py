"""Login users for the database roles (PLAN.md §4; migration 0002).

Migration 0002 creates the NOLOGIN group roles. Each deployment's processes connect as login
users that belong to them, created here from DB_LOGIN_PASSWORDS by the role that runs the
migrations: the API as `discovery_api_login`, the worker's crawl stages as
`discovery_crawl_login`, its scoring stage as `discovery_score_login`. Re-running sets the
passwords again, so rotating one is: change it, rerun, restart.
"""

from collections.abc import Mapping

import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

GROUP_ROLES = ("discovery_crawl", "discovery_score", "discovery_api")


class LoginError(Exception):
    """DB_LOGIN_PASSWORDS names a role that isn't one of GROUP_ROLES."""


def login_name(role: str) -> str:
    return f"{role}_login"


async def ensure_logins(conn: AsyncConnection, passwords: Mapping[str, SecretStr]) -> list[str]:
    """Create or update a login user per role in `passwords`; returns their names."""
    if unknown := sorted(set(passwords) - set(GROUP_ROLES)):
        raise LoginError(f"not a group role: {unknown}; expected some of {GROUP_ROLES}")
    names = []
    for role in GROUP_ROLES:
        if role not in passwords:
            continue
        name = login_name(role)
        exists = await conn.scalar(
            sa.text("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = :name)"), {"name": name}
        )
        # Identifiers come from GROUP_ROLES; the password is quoted by Postgres itself.
        password = await conn.scalar(
            sa.text("SELECT quote_literal(:password)"),
            {"password": passwords[role].get_secret_value()},
        )
        verb = "ALTER" if exists else "CREATE"
        await conn.execute(sa.text(f"{verb} ROLE {name} LOGIN PASSWORD {password}"))
        await conn.execute(sa.text(f"GRANT {role} TO {name}"))
        names.append(name)
    return names
