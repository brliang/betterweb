import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Async tests (marked `anyio`) run on asyncio only."""
    return "asyncio"
