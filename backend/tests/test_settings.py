from datetime import time

import pytest
from pydantic import ValidationError

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
    assert s.exploration_pct == 0.20
    assert s.exploration_split_semantic == 0.5
    assert s.recency_half_life_days == 7
    assert s.max_per_domain_per_page == 2
    assert s.provider_monthly_spend_cap_usd == 40


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
