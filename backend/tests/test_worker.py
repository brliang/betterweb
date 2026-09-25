import io
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


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("fresh_settings")
def test_calendar_is_the_start_time_in_the_users_timezone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CYCLE_LOCAL_START", "01:00")
    monkeypatch.setenv("CYCLE_TIMEZONE", "America/New_York")
    assert main(["cycle", "calendar"]) == 0
    assert capsys.readouterr().out == "*-*-* 01:00:00 America/New_York\n"


def test_only_some_stages_can_be_rerun() -> None:
    with pytest.raises(SystemExit):  # argparse: invalid choice
        main(["cycle", "run", "--stage", "fetch"])


@pytest.mark.usefixtures("fresh_settings")
def test_an_alert_with_no_recipients_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALERT_EMAIL_TO", "[]")
    monkeypatch.setattr("sys.stdin", io.StringIO("log lines"))
    assert main(["alert", "send", "cycle failed"]) == 0


@pytest.mark.usefixtures("fresh_settings")
def test_an_alert_without_a_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_EMAIL_TO", '["me@example.com"]')
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setattr("sys.stdin", io.StringIO("log lines"))
    assert main(["alert", "send", "cycle failed"]) == 1
