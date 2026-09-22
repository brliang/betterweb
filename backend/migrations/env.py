"""Alembic environment.

The CLI (`make migrate`, `make migration`) connects with DATABASE_URL from settings. Tests
pass an open connection in `config.attributes["connection"]` instead.
"""

import logging
from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import Connection, create_engine, pool

from app.db import models
from app.db.base import USR, WEB
from app.settings import get_settings

config = context.config
target_metadata = models.Base.metadata
MANAGED_SCHEMAS = {WEB, USR}


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    # Autogenerate only compares our schemas, not `public` (pgvector, alembic_version).
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | Literal[False]:
    # Render pgvector columns as `VECTOR(...)` with an import, not the internal module path.
    if type_ == "type" and isinstance(obj, VECTOR):
        autogen_context.imports.add("from pgvector.sqlalchemy import VECTOR")
        return f"VECTOR({obj.dim or ''})"
    return False


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        render_item=render_item,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection: Connection | None = config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")
    url = get_settings().database_url.get_secret_value()
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        run_migrations(connection)


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported; run against a database.")
run_migrations_online()
