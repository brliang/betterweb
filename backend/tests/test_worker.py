from collections.abc import Iterator

import pytest

from app.settings import get_settings
from app.worker.__main__ import main


@pytest.fixture
def no_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Environment variables beat backend/.env, so a developer's real key can't leak in.
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cycle_run_is_not_implemented_yet() -> None:
    assert main(["cycle", "run"]) == 1


@pytest.mark.usefixtures("no_api_key")
def test_embed_without_a_key_fails_cleanly() -> None:
    assert main(["taxonomy", "embed"]) == 1
