"""Every tunable threshold, weight, limit and budget lives here (PLAN.md §1 principle 5, §5).

Each field can be overridden by an environment variable of the same name, case-insensitive
(e.g. ``MAX_EXTERNAL_HOPS=2``), or by a ``.env`` file in the working directory.
"""

from datetime import time
from functools import lru_cache
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import DocumentType, InterestLevel, RankingPreset


class RankingWeights(BaseModel):
    """Weights of the ranking components (PLAN.md §6.6); all non-negative, the hide penalty
    is subtracted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interest: float = Field(ge=0)
    ppr: float = Field(ge=0)
    feedback: float = Field(ge=0)
    recency: float = Field(ge=0)
    hide: float = Field(ge=0)


class TokenPrices(BaseModel):
    """A chat model's price in USD per million tokens (see openrouter.ai/models)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: float = Field(ge=0)
    output: float = Field(ge=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, use_attribute_docstrings=True
    )

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://discovery:discovery@localhost:5432/discovery"
    )
    """Contains a password, so it is masked in logs and reprs; read with .get_secret_value().
    A deployment connects each process as its own role (PLAN.md §4): the API as a member of
    discovery_api, the worker as one of discovery_crawl."""
    score_database_url: SecretStr | None = None
    """The worker's connection for the scoring stage, as a member of discovery_score, the only
    cycle stage that reads `usr`. DATABASE_URL when unset (local development)."""
    db_login_passwords: dict[str, SecretStr] = {}
    """Passwords of the login users `db logins` creates, by group role (`{"discovery_api":
    "..."}`): each becomes `<role>_login`, a member of that role. Set only where migrations run."""

    # Crawl reach
    max_internal_depth: int = Field(default=5, ge=0)
    """Link-clicks within one domain from its entry point."""
    max_external_hops: int = Field(default=1, ge=0)
    """Domain jumps from the nearest pinned seed."""
    skip_path_segments: list[str] = [
        "account",
        "accounts",
        "cart",
        "checkout",
        "forgot-password",
        "login",
        "logout",
        "password-reset",
        "register",
        "sign-in",
        "sign-up",
        "signin",
        "signup",
        "subscribe",
        "subscription",
        "subscriptions",
    ]
    """URLs with a path segment among these (case-insensitive, trailing digits ignored, so
    `login2` matches) are never enqueued: account pages the bot can't use."""

    # Crawl cycle
    cycle_page_budget: int = Field(default=20_000, gt=0)
    cycle_fetch_rounds: int = Field(default=3, ge=1)
    """Rounds of fetch then extract per cycle, each fetching the links the last one found: the
    crawl reaches this many link levels further per night, within the budget and time limit."""
    cycle_recrawl_share: float = Field(default=0.2, ge=0, le=1)
    """Share of the page budget reserved for re-crawls."""
    cycle_time_limit_h: float = Field(default=4, gt=0)
    """Hard stop for the fetch stage."""
    cycle_local_start: time = time(2, 0)
    """Nightly start time, in CYCLE_TIMEZONE; `cycle calendar` turns both into the systemd
    timer's schedule."""
    cycle_timezone: str = "UTC"
    """IANA name of the timezone CYCLE_LOCAL_START is in: the V0 user's."""

    # Politeness
    per_domain_min_delay_s: float = Field(default=1.0, ge=0)
    """Between requests to one registrable domain; a larger robots.txt Crawl-delay (the largest
    of its hosts') is used instead."""
    per_domain_concurrency: int = Field(default=1, ge=1)
    """Requests at once to one registrable domain (every `*.bearblog.dev` blog is one), which
    also shares its pace: the delay and backoff."""
    global_concurrency: int = Field(default=50, ge=1)
    max_page_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    user_agent: str = "bribot/0.1 (+https://example.invalid/bot)"
    """The contact URL is a placeholder until the bot's contact page exists (PLAN.md §14 Q5);
    the worker refuses to crawl while it points at example.invalid. The product token before
    the `/` is the name robots.txt groups and robots meta tags address."""
    allow_private_addresses: bool = False
    """Let the crawler connect to loopback, private and other non-public addresses (PLAN.md
    §14 Q6). Only for local end-to-end checks against a test server; never in a deployment."""
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
    """Newest sitemap entries enqueued per domain per cycle; entries with a news publication
    date go first."""
    sitemap_max_files_per_domain: int = Field(default=10, ge=1)
    """Sitemap files (including index children) fetched per domain per cycle."""
    sitemap_max_age_days: float = Field(default=30, gt=0)
    """Sitemap entries whose lastmod is older than this are skipped."""
    sitemap_max_external_hops: int = Field(default=0, ge=0)
    """Sitemaps are polled only for domains this close to a seed; a big external site's
    sitemap would otherwise flood the frontier."""
    sitemap_skip_words: list[str] = [
        "author",
        "authors",
        "categories",
        "category",
        "cities",
        "city",
        "collections",
        "contributor",
        "contributors",
        "landing",
        "location",
        "locations",
        "player",
        "players",
        "region",
        "regions",
        "roster",
        "rosters",
        "schedule",
        "schedules",
        "standings",
        "stats",
        "subscription",
        "tag",
        "tagpages",
        "tags",
        "team",
        "teams",
        "topic",
        "topics",
        "weather",
    ]
    """Sitemaps whose path has one of these words (`sitemap-authors.xml`, `/tags/sitemap.xml`)
    list hub or reference pages, not articles, so they aren't read."""

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

    # Scoring stage (PLAN.md §6.5)
    ppr_damping: float = Field(default=0.85, gt=0, lt=1)
    """Chance of following a link rather than jumping back to the seeds."""
    ppr_tol: float = Field(default=1e-6, gt=0)
    """Power iteration stops when no score vector changes by more than this (L1) in one step."""
    ppr_max_iter: int = Field(default=100, ge=1)
    """Power iteration stops here even if not converged; the stage logs and records it."""
    user_ppr_top_k: int = Field(default=50_000, ge=1)
    """Highest-scoring documents stored per user in usr.user_ppr."""
    liked_half_life_days: float = Field(default=90, gt=0)
    """A like counts half as much in the `liked` profile vector after this many days."""

    # Ranking (PLAN.md §6.6)
    ranking_presets: dict[RankingPreset, RankingWeights] = {
        RankingPreset.BALANCED: RankingWeights(
            interest=1.0, ppr=1.0, feedback=0.5, recency=0.5, hide=1.0
        ),
        RankingPreset.TRUST_MY_SOURCES: RankingWeights(
            interest=0.5, ppr=2.0, feedback=0.5, recency=0.5, hide=1.0
        ),
        RankingPreset.MATCH_MY_INTERESTS: RankingWeights(
            interest=2.0, ppr=0.5, feedback=0.5, recency=0.5, hide=1.0
        ),
        RankingPreset.FRESH: RankingWeights(
            interest=0.5, ppr=0.5, feedback=0.5, recency=2.0, hide=1.0
        ),
    }
    """Component weights of each survey preset; a user's ranking follows their preset's
    current weights."""
    default_preset: RankingPreset = RankingPreset.BALANCED
    search_query_weight: float = Field(default=5.0, ge=0)
    """w_q: weight of query similarity in search, far above the other weights."""
    interest_level_weights: dict[InterestLevel, float] = {
        InterestLevel.INTERESTED: 1.0,
        InterestLevel.VERY_INTERESTED: 2.0,
    }
    """user_interests.weight for each survey answer."""
    interest_topic_blend: float = Field(default=0.5, ge=0, le=1)
    """Share of topic-tag overlap in interest similarity; the rest is embedding similarity."""
    recency_half_life_days: float = Field(default=7, gt=0)
    evergreen_half_life_days: float = Field(default=90, gt=0)
    """Recency half-life of EVERGREEN_TYPES, which stay relevant longer."""
    evergreen_types: list[DocumentType] = [DocumentType.PAPER, DocumentType.PDF]
    hide_penalty_threshold: float = Field(default=0.75, ge=0, lt=1)
    """Cosine similarity to a hidden document above which a candidate is penalized; the penalty
    grows linearly from 0 here to 1 at identical."""
    profile_max_documents: int = Field(default=200, ge=1)
    """Most recent liked (and hidden) documents compared against candidates, for the hide
    penalty and "similar to ... you liked"."""
    impression_max_unclicked: int = Field(default=3, ge=0)
    """A document shown more often than this (distinct recommendations) without a click is
    filtered out of the feed."""
    candidates_per_source: int = Field(default=500, ge=1)
    """Top-N documents taken from each candidate source (PPR, each profile vector, recent
    pinned, each exploration route, search)."""
    feed_page_size: int = Field(default=20, ge=1)
    max_per_domain_per_page: int = Field(default=2, ge=1)
    """Diversity cap per feed page, unless no other domain has candidates left."""
    exploration_pct: float = Field(default=0.20, ge=0, le=1)
    """Default feed share for exploration; the user's survey answer overrides it."""
    exploration_choices: list[float] = [0.10, 0.20, 0.35]
    """Exploration shares the survey and settings offer (PLAN.md §7 step 4)."""
    exploration_split_semantic: float = Field(default=0.5, ge=0, le=1)
    """Semantic share of exploration slots; the remainder is graph-adjacent."""
    explore_band_skip: int = Field(default=500, ge=0)
    """Semantic exploration skips this many nearest documents to the interest vector (the
    top matches, which the main slice covers)..."""
    explore_band_size: int = Field(default=1000, ge=1)
    """...and takes the next this many: similar, but not a top match."""
    explore_graph_max_hops: int = Field(default=2, ge=1)
    """Graph exploration reaches domains up to this many domain-level links from a pin."""
    default_content_types: list[DocumentType] = [
        kind for kind in DocumentType if kind != DocumentType.PAGE
    ]
    """Before the survey is taken. Plain pages (homepages, listings) are opt-in."""
    reasons_max: int = Field(default=3, ge=1)
    """Plain-language reasons shown per item (PLAN.md §6.7)."""
    reason_min_contribution: float = Field(default=0.1, ge=0)
    """A component contributing less than this (weight x normalized value) gives no reason."""
    reason_examples_max: int = Field(default=2, ge=1)
    """Domains named in a "Linked from ..." reason."""
    search_query_max_chars: int = Field(default=500, ge=1)
    query_embedding_cache_size: int = Field(default=256, ge=0)
    """Search query embeddings kept in memory, so paging through results costs no request."""
    feedback_reason_max_chars: int = Field(default=2000, ge=1)
    """Longest answer to "What did you like about it?"."""
    events_max_batch: int = Field(default=200, ge=1)
    """Most events accepted in one POST /events."""
    metrics_days: int = Field(default=30, ge=1)
    """Days of daily numbers in GET /admin/metrics."""
    admin_cycles_shown: int = Field(default=60, ge=1)
    """Crawl cycles listed by GET /admin/cycles."""

    # Auth (PLAN.md §8): one-time login links from the worker CLI, then a session cookie.
    app_base_url: str = "http://localhost:5173"
    """Where the frontend is served; login links point at its /login page."""
    login_token_ttl_minutes: float = Field(default=30, gt=0)
    session_ttl_days: float = Field(default=30, gt=0)
    session_cookie_name: str = "session"
    session_cookie_secure: bool = True
    """Send the cookie over HTTPS only; set false for plain-http local development."""
    admin_emails: list[str] = []
    """Users who may see /admin (the author). Compared lowercased."""

    # Alerts (PLAN.md §10): email when a cycle or backup fails, or the disk fills up.
    alert_email_to: list[str] = []
    """Who gets alerts; none are sent while this is empty."""
    alert_email_from: str = "betterweb <onboarding@resend.dev>"
    """Resend's shared sender, which may send only to the Resend account's own address; a
    verified domain's address can send to anyone."""
    resend_api_key: SecretStr | None = None
    """Alerts go through Resend's HTTP API: DigitalOcean blocks outgoing SMTP from droplets."""
    resend_base_url: str = "https://api.resend.com"
    alert_max_body_chars: int = Field(default=20_000, ge=1)
    """Longest alert body (log lines); the end is kept, since that's where a failure shows."""

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
    taxonomy_description_budget_usd: float = Field(default=2.0, ge=0)
    """Most one run of scripts.describe_topics may spend. It runs on a developer's machine and
    its output is committed, so it has its own budget instead of a deployment's ledger."""
    llm_concurrency: int = Field(default=8, ge=1)
    """Parallel LLM requests for batch jobs such as topic descriptions."""
    llm_prices: dict[str, TokenPrices] = {
        "anthropic/claude-sonnet-5": TokenPrices(input=2.0, output=10.0),
    }
    """Chat model prices by model. Estimates each request before it is sent (so the spend cap
    holds) and costs it when the provider reports no cost. Every configured model needs one."""

    # Opt-in summaries (PLAN.md §6.8)
    summary_model: str = "anthropic/claude-sonnet-5"
    """Writes "Why might I like this?" summaries. Cached per model, so a change writes new ones."""
    summary_max_tokens: int = Field(default=200, ge=1)
    """Room for two sentences; a longer reply is an error rather than a cut-off summary."""
    summary_text_max_chars: int = Field(default=1500, ge=0)
    """Characters of the document's text in the prompt, after its title and excerpt."""
    summary_max_interests: int = Field(default=12, ge=0)
    """Most interest names in the prompt: those the document falls under first, then the
    strongest."""

    @model_validator(mode="after")
    def _every_preset_and_level_has_a_weight(self) -> Self:
        missing = [
            *(set(RankingPreset) - self.ranking_presets.keys()),
            *(set(InterestLevel) - self.interest_level_weights.keys()),
        ]
        if missing:
            raise ValueError(f"no weights for {sorted(missing)}")
        return self

    @field_validator("cycle_timezone")
    @classmethod
    def _known_timezone(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown timezone {name!r}") from error
        return name

    @model_validator(mode="after")
    def _every_chat_model_has_a_price(self) -> Self:
        unpriced = {self.summary_model, self.taxonomy_description_model} - self.llm_prices.keys()
        if unpriced:
            raise ValueError(f"no LLM_PRICES for {sorted(unpriced)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
