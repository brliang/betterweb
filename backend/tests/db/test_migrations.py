from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.pool import NullPool

from tests.db.database import alembic_config, create_database, database_url, drop_database


@pytest.fixture
def empty_database() -> Iterator[URL]:
    url = database_url("_migrations")
    create_database(url)
    yield url
    drop_database(url)


def test_models_match_migrations(engine: Engine) -> None:
    # Fails when a model changed without a migration (`make migration name=...`).
    with engine.connect() as connection:
        command.check(alembic_config(connection))


def test_migrations_downgrade_cleanly_and_reapply(empty_database: URL) -> None:
    engine = create_engine(empty_database, poolclass=NullPool)
    with engine.begin() as connection:
        config = alembic_config(connection)
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        schemas = connection.scalars(
            text("SELECT nspname FROM pg_namespace WHERE nspname IN ('web', 'usr')")
        ).all()
        leftover_default_grants = connection.scalar(text("SELECT count(*) FROM pg_default_acl"))
        assert schemas == []
        assert leftover_default_grants == 0

        command.upgrade(config, "head")
    engine.dispose()
