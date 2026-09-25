from datetime import time

import pytest
from pydantic import ValidationError

from app.enums import RankingPreset
from app.settings import Settings, TokenPrices


def test_defaults_match_plan() -> None:
    s = Settings(_env_file=None)
    assert s.max_internal_depth == 5
    assert s.max_external_hops == 1
    assert s.cycle_page_budget == 20_000
    assert s.cycle_recrawl_share == 0.2
    assert s.cycle_time_limit_h == 4
    assert s.cycle_local_start == time(2, 0)
    assert s.allow_private_addresses is False  # PLAN.md §14 Q6
    assert s.per_domain_min_delay_s == 1.0
    assert s.per_domain_concurrency == 1
    assert s.global_concurrency == 50
    assert s.max_page_bytes == 5 * 1024 * 1024
    assert s.ppr_damping == 0.85
    assert s.ppr_tol == 1e-6
    assert s.ppr_max_iter == 100
    assert (s.user_ppr_top_k, s.liked_half_life_days) == (50_000, 90)
    assert s.exploration_pct == 0.20
    assert s.exploration_split_semantic == 0.5
    assert s.recency_half_life_days == 7
    assert s.max_per_domain_per_page == 2
    assert s.provider_monthly_spend_cap_usd == 40
    assert s.embedding_model == "qwen/qwen3-embedding-8b"
    assert s.openrouter_api_key is None
    assert s.robots_ttl_h == 24
    assert s.recrawl_after_h < 24  # so the next nightly cycle sees yesterday's fetches
    assert s.domain_prior_weight == 1.0
    assert s.sitemap_max_external_hops == 0
    assert {"utm_*", "fbclid", "gclid", "ref", "mc_*"} <= set(s.tracking_params)
    assert s.user_agent.startswith("bribot/")
    assert s.dedup_min_confidence == 0.9
    assert (s.embedding_batch_size, s.embed_text_max_chars) == (64, 2000)
    assert (s.embedding_usd_per_mtok, s.provider_chars_per_token) == (0.01, 3.0)
    assert (s.tag_max_topics, s.tag_min_similarity, s.tag_max_gap) == (3, 0.2, 0.05)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_EXTERNAL_HOPS", "2")
    monkeypatch.setenv("CYCLE_LOCAL_START", "03:30")
    s = Settings(_env_file=None)
    assert s.max_external_hops == 2
    assert s.cycle_local_start == time(3, 30)


@pytest.mark.parametrize(
    ("name", "value"),
    [("ppr_damping", "1.5"), ("cycle_recrawl_share", "-0.1"), ("global_concurrency", "0")],
)
def test_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name.upper(), value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_url_is_masked() -> None:
    s = Settings(_env_file=None)
    assert "discovery:discovery" not in repr(s)
    assert s.database_url.get_secret_value().startswith("postgresql+psycopg://")


def test_fields_are_documented() -> None:
    assert Settings.model_fields["max_internal_depth"].description == (
        "Link-clicks within one domain from its entry point."
    )


def test_every_preset_and_interest_level_needs_weights() -> None:
    defaults = Settings(_env_file=None)
    partial = {RankingPreset.BALANCED: defaults.ranking_presets[RankingPreset.BALANCED]}
    with pytest.raises(ValidationError, match="no weights for"):
        Settings(_env_file=None, ranking_presets=partial)


def test_every_chat_model_needs_a_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_MODEL", "some/other-model")
    with pytest.raises(ValidationError, match="no LLM_PRICES for"):
        Settings(_env_file=None)
    monkeypatch.setenv(
        "LLM_PRICES",
        '{"some/other-model": {"input": 1, "output": 2},'
        ' "anthropic/claude-sonnet-5": {"input": 2, "output": 10}}',
    )
    assert Settings(_env_file=None).llm_prices["some/other-model"] == TokenPrices(input=1, output=2)


def test_cycle_timezone_must_exist() -> None:
    with pytest.raises(ValidationError, match="unknown timezone"):
        Settings(_env_file=None, cycle_timezone="America/Nowhere")


def test_db_login_passwords_come_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_LOGIN_PASSWORDS", '{"discovery_api": "secret"}')
    passwords = Settings(_env_file=None).db_login_passwords
    assert passwords["discovery_api"].get_secret_value() == "secret"
    assert "secret" not in repr(passwords)
