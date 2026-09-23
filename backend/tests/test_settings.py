from datetime import time

import pytest
from pydantic import ValidationError

from app.enums import RankingPreset
from app.settings import Settings


def test_defaults_match_plan() -> None:
    s = Settings(_env_file=None)
    assert s.max_internal_depth == 5
    assert s.max_external_hops == 1
    assert s.cycle_page_budget == 20_000
    assert s.cycle_recrawl_share == 0.2
    assert s.cycle_time_limit_h == 4
    assert s.cycle_local_start == time(2, 0)
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
