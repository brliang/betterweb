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


@pytest.fixture
def placeholder_user_agent(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("USER_AGENT", "bribot/0.1 (+https://example.invalid/bot)")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("placeholder_user_agent")
def test_cycle_run_refuses_the_placeholder_user_agent() -> None:
    # PLAN.md principle 4: no crawling without a real contact page in the user agent.
    assert main(["cycle", "run"]) == 1


@pytest.mark.usefixtures("no_api_key")
def test_cycle_run_needs_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_AGENT", "bribot/0.1 (+https://bot.example/)")
    get_settings.cache_clear()
    assert main(["cycle", "run"]) == 1


def test_seed_rejects_an_uncrawlable_url() -> None:
    assert main(["frontier", "seed", "mailto:someone@example.com"]) == 1


@pytest.mark.usefixtures("no_api_key")
def test_embed_without_a_key_fails_cleanly() -> None:
    assert main(["taxonomy", "embed"]) == 1
