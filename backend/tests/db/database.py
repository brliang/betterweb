"""Creating, dropping and migrating test databases."""

import io
import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import URL, Connection, create_engine, make_url, text
from sqlalchemy.pool import NullPool

from app.settings import get_settings

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def database_url(name_suffix: str = "") -> URL:
    """TEST_DATABASE_URL if set, else the app database with `_test` appended to its name."""
    if override := os.environ.get("TEST_DATABASE_URL"):
        url = make_url(override)
    else:
        app_url = make_url(get_settings().database_url.get_secret_value())
        url = app_url.set(database=f"{app_url.database}_test")
    return url.set(database=f"{url.database}{name_suffix}")


def _admin_execute(url: URL, *statements: str) -> None:
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    with admin.connect() as connection:
        for statement in statements:
            connection.execute(text(statement))
    admin.dispose()


def create_database(url: URL) -> None:
    name = f'"{url.database}"'
    _admin_execute(url, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"CREATE DATABASE {name}")


def drop_database(url: URL) -> None:
    _admin_execute(url, f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')


def alembic_config(connection: Connection) -> Config:
    # stdout is captured so `alembic check` doesn't print into the test output.
    return Config(toml_file=PYPROJECT, stdout=io.StringIO(), attributes={"connection": connection})
