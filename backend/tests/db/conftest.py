"""Fixtures for tests that need Postgres: the Compose `db` service locally, a service container
in CI. Never SQLite.

Each session creates a fresh database (the app database's name plus `_test`, or
TEST_DATABASE_URL) and migrates it to head. Each test runs in a transaction that is rolled
back, so tests don't see each other's rows.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.db.database import alembic_config, create_database, database_url, drop_database


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = database_url()
    create_database(url)
    engine = create_engine(url, poolclass=NullPool)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "head")
    yield engine
    engine.dispose()
    drop_database(url)


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session(engine: Engine) -> AsyncIterator[AsyncSession]:
    async_engine = create_async_engine(engine.url, poolclass=NullPool)
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        # Commits inside the test release a savepoint; the outer transaction still rolls back.
        yield AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        await transaction.rollback()
    await async_engine.dispose()
