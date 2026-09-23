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
    user_agent: str = "bribot/0.1 (+https://example.invalid/bot)"
    """The contact URL is a placeholder until the bot's contact page exists (PLAN.md §14 Q5);
    the worker refuses to crawl while it points at example.invalid. The product token before
    the `/` is the name robots.txt groups and robots meta tags address."""
    fetch_timeout_s: float = Field(default=30, gt=0)
    robots_ttl_h: float = Field(default=24, gt=0)
    """How long a fetched robots.txt is trusted before it is fetched again."""
    domain_backoff_max_s: float = Field(default=600, ge=0)
    """Longest pause for a domain after 429/5xx responses (delays double per error)."""
    domain_max_consecutive_errors: int = Field(default=5, ge=1)
    """After this many 429/5xx/timeouts in a row, a domain is skipped for the rest of the cycle."""

    # Frontier (PLAN.md §6.2)
    recrawl_after_h: float = Field(default=20, gt=0)
    """A fetched URL is due for re-crawl this long after its last fetch (under a day, so the
    next nightly cycle sees it)."""
    fetch_retry_base_h: float = Field(default=20, gt=0)
    """First retry delay for a URL whose fetch failed transiently; doubles per failure."""
    fetch_max_failures: int = Field(default=4, ge=1)
    """Transient failures in a row before a URL is dropped from the frontier."""
    domain_prior_weight: float = Field(default=1.0, ge=0)
    """λ in the frontier priority: weight of the domain score next to in-link PageRank mass."""
    tracking_params: list[str] = [
        "utm_*",
        "mc_*",
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "igshid",
        "ref",
        "ref_src",
        "_hsenc",
        "_hsmi",
    ]
    """Query parameters removed by URL canonicalization; a trailing `*` matches a prefix."""

    # Feeds and sitemaps (PLAN.md §6.1 step 1.1)
    feed_max_items: int = Field(default=100, ge=1)
    """Newest entries enqueued per feed poll."""
    sitemap_max_urls_per_domain: int = Field(default=500, ge=1)
    """Newest sitemap entries enqueued per domain per cycle."""
    sitemap_max_files_per_domain: int = Field(default=10, ge=1)
    """Sitemap files (including index children) fetched per domain per cycle."""
    sitemap_max_age_days: float = Field(default=30, gt=0)
    """Sitemap entries whose lastmod is older than this are skipped."""
    sitemap_max_external_hops: int = Field(default=0, ge=0)
    """Sitemaps are polled only for domains this close to a seed; a big external site's
    sitemap would otherwise flood the frontier."""

    # Extraction and dedup (PLAN.md §6.3)
    extract_max_text_chars: int = Field(default=200_000, ge=1)
    """Extracted text beyond this is cut off before it is stored."""
    extract_excerpt_chars: int = Field(default=300, ge=1)
    """Excerpt length when the page offers no description of its own."""
    extract_max_links_per_page: int = Field(default=500, ge=0)
    """Links kept per page, in page order; they become graph edges and frontier candidates."""
    extract_anchor_max_chars: int = Field(default=200, ge=0)
    """Anchor text stored per link."""
    extract_field_max_chars: int = Field(default=500, ge=1)
    """Longest title or author stored; longer ones are cut off."""
    pdf_max_pages: int = Field(default=50, ge=1)
    """PDF pages read for text."""
    dedup_min_confidence: float = Field(default=0.9, ge=0, le=1)
    """A dedup strategy's match counts only at or above this confidence."""
    dedup_hash_min_words: int = Field(default=50, ge=1)
    """Texts shorter than this never match by hash: short pages ("Page not found", a video's
    empty body) would otherwise all merge into one document."""

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

    # Model providers (PLAN.md §6.4, §6.8). Never send user identifiers to a provider.
    provider_monthly_spend_cap_usd: float = Field(default=40, ge=0)
    """Hard stop on combined embedding + LLM spend."""
    openrouter_api_key: SecretStr | None = None
    """One key for embeddings and LLM calls; set a monthly limit on it in OpenRouter too."""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    provider_timeout_s: float = Field(default=60, gt=0)
    provider_max_retries: int = Field(default=3, ge=0)
    """Retries on 429 and 5xx responses, with exponential backoff."""
    provider_retry_base_delay_s: float = Field(default=1.0, ge=0)
    """First backoff delay; doubles after each retry."""
    embedding_model: str = "qwen/qwen3-embedding-8b"
    """Changing it means re-embedding everything; the vector size is fixed in the schema."""
    embedding_batch_size: int = Field(default=64, ge=1)
    """Texts per embeddings request; the embed stage commits one request's documents at a time."""
    embedding_usd_per_mtok: float = Field(default=0.01, ge=0)
    """The embedding model's price per million input tokens. Estimates each request before it
    is sent (so the spend cap holds) and costs it when the provider reports no cost."""
    provider_chars_per_token: float = Field(default=3.0, gt=0)
    """Characters per token for those estimates; English averages about 4, so 3 overestimates."""
    embed_text_max_chars: int = Field(default=2000, ge=0)
    """Characters of document text embedded after its title and excerpt (about 512 tokens)."""
    tag_max_topics: int = Field(default=3, ge=0)
    """Most topics per document (PLAN.md §6.4)."""
    tag_min_similarity: float = Field(default=0.2, ge=-1, le=1)
    """Cosine similarity any topic needs to tag a document. Measured on Qwen3: the right topic
    scores about 0.24-0.44, the median topic about 0.1."""
    tag_max_gap: float = Field(default=0.05, ge=0)
    """A topic tags a document only within this similarity of the document's best topic, which
    keeps close runners-up and drops the stray matches an absolute threshold lets through."""
    search_query_instruction: str = (
        "Given a web search query, retrieve relevant web pages that answer the query"
    )
    """Qwen3 embeds queries as `Instruct: <this>\\nQuery:<query>`; documents get no prefix."""
    topic_embedding_instruction: str = (
        "Given a content category and its description, retrieve web pages that belong to this "
        "category"
    )
    """Topics are embedded as queries against plain document embeddings; far better tagging
    than embedding both sides plainly. Changing it needs `taxonomy embed --all`."""
    taxonomy_description_model: str = "anthropic/claude-sonnet-5"
    """Writes the one-off topic descriptions in backend/data/taxonomy (PLAN.md §6.4)."""
    llm_concurrency: int = Field(default=8, ge=1)
    """Parallel LLM requests for batch jobs such as topic descriptions."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
