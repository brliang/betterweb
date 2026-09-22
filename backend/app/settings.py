"""Every tunable threshold, weight, limit and budget lives here (PLAN.md §1 principle 5, §5).

Each field can be overridden by an environment variable of the same name, case-insensitive
(e.g. ``MAX_EXTERNAL_HOPS=2``), or by a ``.env`` file in the working directory.
"""

from datetime import time
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, use_attribute_docstrings=True
    )

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://discovery:discovery@localhost:5432/discovery"
    )
    """Contains a password, so it is masked in logs and reprs; read with .get_secret_value()."""

    # Crawl reach
    max_internal_depth: int = Field(default=5, ge=0)
    """Link-clicks within one domain from its entry point."""
    max_external_hops: int = Field(default=1, ge=0)
    """Domain jumps from the nearest pinned seed."""

    # Crawl cycle
    cycle_page_budget: int = Field(default=20_000, gt=0)
    cycle_recrawl_share: float = Field(default=0.2, ge=0, le=1)
    """Share of the page budget reserved for re-crawls."""
    cycle_time_limit_h: float = Field(default=4, gt=0)
    """Hard stop for the fetch stage."""
    cycle_local_start: time = time(2, 0)
    """Nightly start time, in the user's timezone."""

    # Politeness
    per_domain_min_delay_s: float = Field(default=1.0, ge=0)
    """Used unless robots.txt Crawl-delay is larger."""
    per_domain_concurrency: int = Field(default=1, ge=1)
    global_concurrency: int = Field(default=50, ge=1)
    max_page_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    user_agent: str = "DiscoveryBot/0.1 (+https://example.invalid/bot)"
    """Placeholder until the project name and bot contact page exist (PLAN.md §14 Q5)."""

    # Personalized PageRank
    ppr_damping: float = Field(default=0.85, gt=0, lt=1)
    ppr_tol: float = Field(default=1e-6, gt=0)
    ppr_max_iter: int = Field(default=100, ge=1)

    # Ranking
    exploration_pct: float = Field(default=0.20, ge=0, le=1)
    """Default feed share for exploration; the user's survey answer overrides it."""
    exploration_split_semantic: float = Field(default=0.5, ge=0, le=1)
    """Semantic share of exploration slots; the remainder is graph-adjacent."""
    recency_half_life_days: float = Field(default=7, gt=0)
    max_per_domain_per_page: int = Field(default=2, ge=1)
    """Diversity cap per 20 results."""

    # Model providers
    provider_monthly_spend_cap_usd: float = Field(default=40, ge=0)
    """Hard stop on combined embedding + LLM spend."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
