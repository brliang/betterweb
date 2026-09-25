from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import Settings


def create_engine(settings: Settings, url: SecretStr | None = None) -> AsyncEngine:
    """An engine for DATABASE_URL, or for `url` (another role's connection)."""
    url = url or settings.database_url
    return create_async_engine(url.get_secret_value(), pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
