# Discovery Engine — Implementation Plan

A graph-centric, open-source content discovery and search engine. Users pin sites they trust; the system crawls outward from them, builds a shared web graph, and ranks content with **transparent, user-controllable heuristics**. Every recommendation explains why it was shown.

This document is the source of truth for implementation. Work through V0 milestones in order. Do **not** build V1/V2 features, but **do** honor every "Reserved for V1" note so those phases need no redesign.

---

## 1. Goals and principles

**V0 success criterion:** the single user (the author) finds a few pieces of content per day they would not otherwise have found. Measured as likes/clicks on documents from domains they did not pin (see §9).

**Principles (apply everywhere):**

1. **Transparency.** Every ranking score is decomposable into named components that are stored and shown to the user.
2. **User data is separate from the web graph.** The shared graph is public information about the web. User data lives in a separate store and never gets written into the graph (§4).
3. **Private vs. public signals.** Likes, hides and feedback are private and affect only the user's own ranking. Public signals (a future "recommend" action) are reserved for V1 social features. Never let private signals influence another user's results.
4. **Polite, honest crawling.** Respect robots.txt, rate-limit per domain, and identify with an honest user agent that includes a contact URL.
5. **Everything tunable.** All thresholds, weights, limits and budgets live in one typed settings module with sensible defaults. No magic numbers in code.
6. **Provider-agnostic models.** Embeddings and LLM calls go through interfaces with swappable implementations, so local models can replace hosted providers later. Never send user identifiers to model providers.
7. **Multi-user-ready, single-user-deployed.** V0 has one user, but every user-scoped table and query is keyed by `user_id`.

---

## 2. Tech stack

| Area | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| DB | PostgreSQL 16 + pgvector |
| ORM / migrations | SQLAlchemy 2.x + Alembic |
| Fetching | httpx2 (async; the maintained successor to httpx), asyncio |
| Feeds | feedparser |
| HTML extraction | trafilatura |
| PDF extraction | pypdf (or pdfminer.six) |
| Graph math | scipy.sparse (power iteration) |
| Embeddings | Qwen3-Embedding-8B (open weights, Apache-2.0) via OpenRouter, truncated to 1024 dims (behind `EmbeddingProvider` interface) |
| LLM summaries | Hosted via OpenRouter, model configurable (behind `LLMProvider` interface) |
| Frontend | React 19 + TypeScript + Vite, TanStack Query, React Compiler |
| Styling | Tailwind CSS v4 |
| Typed client | FastAPI's generated OpenAPI schema → **orval** → TypeScript client + TanStack Query hooks |
| Tooling | uv, ruff, mypy (strict), pytest; prettier + eslint + tsc on frontend |
| Local dev | Docker Compose (Postgres + API + worker + frontend) |

**Typed client rule:** the frontend never hand-writes API types. A `make codegen` (or equivalent) script exports the OpenAPI schema and regenerates the client. CI fails if the generated client is stale, so a backend model change that breaks the frontend fails the type-check.

---

## 3. Architecture overview

```
                ┌───────────────────── nightly crawl cycle (worker) ─────────────────────┐
 feeds/sitemaps │ 1 Fetch → 2 Extract/Classify → 3 Dedup → 4 Embed+Tag → 5 Scores (PPR) │
 frontier ─────►│                                                                        │
                └──────────────┬───────────────────────────────────────┬─────────────────┘
                               ▼                                       ▼
                    ┌──────────────────┐                    ┌──────────────────┐
                    │  web schema      │  (read-only for    │  usr schema      │
                    │  shared graph    │◄── ranking) ───────│  user store      │
                    └────────┬─────────┘                    └────────┬─────────┘
                             └──────────────┬────────────────────────┘
                                            ▼
                                  FastAPI (feed, search, why, feedback, survey)
                                            ▼
                                  React app (generated typed client)
```

Two processes share one codebase:
- **api**: FastAPI app serving the frontend.
- **worker**: CLI entry point (`python -m app.worker cycle run`) invoked nightly by a systemd timer or cron at a configured local time. It is also runnable manually, stage by stage.

---

## 4. Data model

Use two Postgres schemas: `web` (shared graph) and `usr` (user store).

- `usr` tables may reference `web` IDs.
- `web` tables must **never** reference `usr`.
- Enforce this with DB roles: the worker's crawl stages use a role with **no access** to `usr`. Only the scoring stage (§6.5) and the API may read `usr`.

### 4.1 `web` — shared graph

**Why Documents are separate from URLs and Domains:** a *Document* is one piece of content, the unit that is ranked, recommended, liked or hidden. It has a `type` (article, thread, paper, PDF, video, page…); do not assume articles. Many URLs can resolve to one Document (syndication, tracking params, AMP, mirrors); the dedup stage owns that mapping. A *Domain* carries trust and, later, publisher ownership.

- **`domains`**
  - Fields: `id`, `host` (normalized, unique), `status`, `robots_txt`, `robots_fetched_at`, `crawl_delay_s`, `feed_urls text[]`, `sitemap_urls text[]` (from robots.txt), `first_seen_at`, `last_crawled_at`.
  - Reserved for V1: `verified_owner_id` (nullable, no FK yet).
- **`urls`**
  - Fields: `id`, `url` (canonicalized, unique), `domain_id`, `document_id` (nullable until deduped), `http_status`, `etag`, `last_modified`, `content_hash`, `redirect_to_url_id` (where the last fetch redirected; dedup maps the URL to the target's document), `first_seen_at`, `last_fetched_at`, `fetch_count`, `change_count`.
- **`documents`**
  - Fields: `id`, `canonical_url_id`, `domain_id`, `type` (enum), `title`, `author`, `published_at`, `language`, `text` (extracted, normalized), `excerpt`, `word_count`, `content_hash`, `created_at`, `updated_at`.
  - Reserved for V1: `simhash bigint` (nullable).
- **`links`**
  - Fields: `src_document_id`, `dst_url_id`, `anchor_text`, `is_internal`.
  - Edges point to **URLs**, not Documents, because the destination may not be crawled yet. Resolve through `urls.document_id` when building the graph.
- **`document_embeddings`**: `document_id`, `model`, `vector vector(N)`, `input_hash` (SHA-256 of the embedded text), `document_updated_at` (the document version last checked against it), `created_at`. Store the model name so a model change triggers re-embedding; a document keeps only its current model's row. HNSW index on `vector` (cosine).
- **`topics`**
  - Fields: `id`, `external_id` (IAB ID), `name`, `parent_id`, `tier`, `description`, `embedding vector(N)`.
  - Holds the adapted taxonomy (§6.4).
- **`document_topics`**: `document_id`, `topic_id`, `score`.
- **`frontier`**
  - Fields: `url_id` (unique), `internal_depth`, `external_hops`, `priority`, `reason` (`new` | `recrawl`), `next_fetch_at`, `enqueued_at`, `cycle_id` (the cycle whose plan includes the entry; cleared when fetched), `failures` (transient failures in a row).
  - Crawl state per URL: a fetched URL keeps its entry as a `recrawl`, so its depth is known when its links are followed. A URL that was fetched and then dropped (gone, rejected, moved permanently, failing) has no entry and is never enqueued again.
- **`raw_pages`**: `url_id`, `cycle_id`, `fetched_at`, `content_type`, `charset`, `robots_tag` (X-Robots-Tag headers), `body`. New or changed bodies from the fetch stage, waiting for the extract stage, which deletes them.
- **`dedup_decisions`**
  - Fields: `id`, `url_id`, `document_id`, `method`, `confidence`, `details jsonb`, `created_at`.
  - Append-only log of every URL→Document merge, so strategies can be audited and tuned.
- **`crawl_cycles`**
  - Fields: `id`, `started_at`, `finished_at`, `status`, `page_budget`, `pages_fetched`, `stats jsonb` (per-stage counts, errors, provider spend).
- **`provider_spend`**: `id`, `created_at`, `cycle_id` (nullable), `purpose` (`embed_documents` | `embed_topics`; M8 and M9 add search and summaries), `model`, `requests`, `tokens`, `cost_usd`, `estimated`. Ledger of model provider spend, summed for the monthly cap. No user identifiers.
- **`global_scores`**: `document_id`, `cycle_id`, `pagerank`. PageRank seeded with *all* users' pins; drives crawl priority. Only documents with a positive score have a row.
- **`domain_scores`**: `domain_id`, `cycle_id`, `score`. Used as the domain prior: the mean `pagerank` of the domain's documents (§6.5).
- Reserved for V1: **`domain_metadata_overrides`**
  - Fields: `domain_id`, `url_pattern`, `field`, `value jsonb`, `created_at`.
  - Create the table in V0 and leave it empty. Ingestion should already apply overrides if rows exist (no-op in V0).

### 4.2 `usr` — user store

- **`users`**: `id`, `email`, `timezone`, `created_at`.
- **`survey_responses`**: `id`, `user_id`, `survey_version`, `answers jsonb`, `created_at`. Keep history; don't overwrite.
- **`user_settings`**
  - Fields: `user_id`, `weights jsonb` (ranking component weights), `exploration_pct`, `exploration_split jsonb` (semantic vs. graph), `content_types text[]`, `summaries_opt_in bool` (default **false**), `updated_at`.
- **`user_interests`**: `user_id`, `topic_id`, `weight`, `source` (`survey` | `learned`).
- **`pins`**: `user_id`, `domain_id`, `source` (`survey` | `suggested`; reserved: `manual`), `created_at`.
- **`feedback`**
  - Fields: `id`, `user_id`, `document_id`, `kind` (`like` | `hide` | `block_domain`), `reason_text` (nullable, optional), `visibility` (`private` only in V0; reserved: `public`), `created_at`.
- **`events`**
  - Append-only interaction log.
  - Fields: `id`, `user_id`, `document_id`, `recommendation_id`, `kind` (`impression` | `click` | `like` | `hide` | `why_open` | `summary_view`), `surface` (`feed` | `search`), `position`, `created_at`.
  - Powers ranking now and publisher analytics later (V2).
- **`recommendations`**
  - Fields: `id`, `user_id`, `document_id`, `surface`, `query` (nullable), `slice` (`main` | `adjacent_semantic` | `adjacent_graph`), `score`, `components jsonb`, `cycle_id`, `created_at`.
  - Stores the full score breakdown for the "why" card.
- **`user_ppr`**: `user_id`, `document_id`, `score`, `cycle_id`. Store top-K per user (configurable, e.g. 50k).
- **`user_profile_vectors`**: `user_id`, `kind` (`interest` | `liked` | `hidden`), `vector`, `updated_at`.
- **`summaries`**: `user_id`, `document_id`, `model`, `text`, `created_at`. A cache of opt-in LLM "why you might like this" summaries.

Deleting a user must be a single operation: delete from every `usr` table by `user_id`. Add a test for it.

---

## 5. Settings (defaults)

All of these live in one Pydantic settings module and can be overridden via environment variables.

| Setting | Default | Notes |
|---|---|---|
| `MAX_INTERNAL_DEPTH` | 5 | Link-clicks within one domain from its entry point |
| `MAX_EXTERNAL_HOPS` | 1 | Domain jumps from nearest pinned seed (try 2 later) |
| `CYCLE_PAGE_BUDGET` | 20,000 | Pages fetched per cycle; tune from measurements |
| `CYCLE_RECRAWL_SHARE` | 0.2 | Budget share reserved for re-crawls |
| `CYCLE_TIME_LIMIT_H` | 4 | Hard stop for the fetch stage |
| `CYCLE_LOCAL_START` | 02:00 | In the user's timezone |
| `PER_DOMAIN_MIN_DELAY_S` | 1.0 | Or robots `Crawl-delay` if larger |
| `PER_DOMAIN_CONCURRENCY` | 1 | |
| `GLOBAL_CONCURRENCY` | 50 | |
| `MAX_PAGE_BYTES` | 5 MB | |
| `PPR_DAMPING` | 0.85 | |
| `PPR_TOL` / `PPR_MAX_ITER` | 1e-6 / 100 | Largest L1 change per step to stop at; iteration cap (the stage records whether it converged) |
| `USER_PPR_TOP_K` | 50,000 | Documents stored per user in `user_ppr` |
| `LIKED_HALF_LIFE_DAYS` | 90 | Recency decay of likes in the `liked` profile vector |
| `EXPLORATION_PCT` | 0.20 | Overridden by survey answer |
| `EXPLORATION_SPLIT` | 50/50 semantic/graph | |
| `RECENCY_HALF_LIFE_DAYS` | 7 | Longer for evergreen types (paper, PDF) |
| `MAX_PER_DOMAIN_PER_PAGE` | 2 | Diversity cap per 20 results |
| `PROVIDER_MONTHLY_SPEND_CAP_USD` | 40 | Hard stop on embedding + LLM spend, per calendar month (UTC) |
| `USER_AGENT` | `bribot/0.1 (+https://<project-url>/bot)` | Contact page required |
| `ROBOTS_TTL_H` | 24 | robots.txt refresh interval |
| `RECRAWL_AFTER_H` | 20 | A fetched URL is due for re-crawl after this (under a day, so each nightly cycle sees it) |
| `DOMAIN_BACKOFF_MAX_S` / `DOMAIN_MAX_CONSECUTIVE_ERRORS` | 600 / 5 | Per-domain backoff cap; errors in a row before skipping the domain this cycle |
| `FETCH_RETRY_BASE_H` / `FETCH_MAX_FAILURES` | 20 / 4 | Per-URL retry delay (doubling) and failures before dropping it |
| `DOMAIN_PRIOR_WEIGHT` | 1.0 | λ in the frontier priority (§6.2) |
| `TRACKING_PARAMS` | `utm_*`, `mc_*`, `fbclid`, `gclid`, `ref`, … | Removed by canonicalization |
| `FEED_MAX_ITEMS` / `SITEMAP_MAX_URLS_PER_DOMAIN` / `SITEMAP_MAX_FILES_PER_DOMAIN` / `SITEMAP_MAX_AGE_DAYS` / `SITEMAP_MAX_EXTERNAL_HOPS` | 100 / 500 / 10 / 30 / 0 | Feed and sitemap polling caps (§6.1) |
| `EXTRACT_MAX_TEXT_CHARS` / `EXTRACT_EXCERPT_CHARS` / `EXTRACT_FIELD_MAX_CHARS` | 200,000 / 300 / 500 | Stored text, excerpt, and title/author lengths (§6.3) |
| `EXTRACT_MAX_LINKS_PER_PAGE` / `EXTRACT_ANCHOR_MAX_CHARS` / `PDF_MAX_PAGES` | 500 / 200 / 50 | Links kept per page, anchor text length, PDF pages read |
| `DEDUP_MIN_CONFIDENCE` / `DEDUP_HASH_MIN_WORDS` | 0.9 / 50 | A strategy's match must reach this confidence; shorter texts never match by hash |
| `EMBEDDING_BATCH_SIZE` / `EMBED_TEXT_MAX_CHARS` | 64 / 2,000 | Documents per embeddings request (and per commit); text embedded after title and excerpt (~512 tokens) |
| `EMBEDDING_USD_PER_MTOK` / `PROVIDER_CHARS_PER_TOKEN` | 0.01 / 3 | Price and a conservative token estimate, to check each request against the cap before sending it |
| `TAG_MAX_TOPICS` / `TAG_MIN_SIMILARITY` / `TAG_MAX_GAP` | 3 / 0.2 / 0.05 | Topics per document: at most 3, above the floor, and within the gap of the document's best topic (§6.4) |

---

## 6. Core algorithms

### 6.1 Crawl cycle

A **crawl cycle** is one nightly batch run with a fixed page budget, recorded in `crawl_cycles`. Stages run in order. Each stage is idempotent and resumable (safe to re-run after a crash) and records its stats.

1. **Fetch**
   1. Poll all feeds and sitemaps for domains in the frontier's reach, and enqueue new items.
      - Feeds of every domain in reach; sitemaps (those robots.txt lists) only within `SITEMAP_MAX_EXTERNAL_HOPS` (default 0, the pinned domains), since a big external site's sitemap would flood the frontier.
      - Capped per poll: the newest `FEED_MAX_ITEMS` feed entries; the newest `SITEMAP_MAX_URLS_PER_DOMAIN` sitemap URLs from at most `SITEMAP_MAX_FILES_PER_DOMAIN` files, skipping entries older than `SITEMAP_MAX_AGE_DAYS`.
   2. Pop URLs from `frontier` in `priority` order until the page budget or time limit is reached.
      - *Planning* marks the chosen entries with the cycle (`frontier.cycle_id`); each domain gets at most as many as its delay allows in the time left.
      - Fetching runs every domain at its own pace, and a global limit of `GLOBAL_CONCURRENCY` requests admits the highest priority first.
      - Entries that need no request (robots.txt disallows them) free their budget, so planning and fetching repeat until the budget, the time limit or the due entries run out.
      - The time limit counts from the cycle's start, including after a resume.
      - Reserve `CYCLE_RECRAWL_SHARE` of the budget for `recrawl` entries.
      - Prioritize re-crawls by `change_count / fetch_count`.
   3. Unfetched entries stay in the frontier for the next cycle.
2. **Extract/Classify**: determine the document type and extract text and metadata (§6.3).
3. **Dedup**: map each URL to a Document (§6.3).
   - Steps 2 and 3 run as one pass (`app.ingest.stage`), one transaction per raw page: the page's document, links, frontier candidates and dedup decisions are written and its raw page deleted together, so a killed stage resumes with the pages still waiting. CPU-bound extraction runs in a worker thread.
   - This pass also **follows links**: a page's links are enqueued one step further from its frontier position (§6.2). Links are followed only when a page is new or changed, and a URL they add is fetched in the next cycle, so the crawl reaches one link level deeper per cycle; feeds and sitemaps (depth 0) are found every cycle.
4. **Embed + Tag**: embed new or changed documents, then assign topics (§6.4).
   - Runs in document-id order, one embeddings request per transaction (vectors, tags and spend together), so a killed stage resumes with the documents still waiting.
   - A document is a candidate when it has no embedding from the configured model or was updated since its embedding was checked; if its embedding input is unchanged it costs no request.
   - When the monthly cap is reached, or the provider fails after its retries, the stage stops and the cycle goes on; the remaining documents wait for the next cycle.
5. **Scores**: compute global PageRank, domain scores and per-user PPR, then refresh user profile vectors (§6.5).
   - Computed in memory and written in one transaction (with the frontier priorities it re-estimates), so a killed stage leaves the previous scores and a re-run starts over.
   - The only cycle stage that reads `usr`; it needs nothing beyond the `discovery_score` role's grants.

Use Postgres as the work queue rather than adding a queue service.
- Planning marks entries with the cycle instead of locking them with `FOR UPDATE SKIP LOCKED`, which Postgres doesn't allow alongside the window functions that cap each domain.
- A Postgres advisory lock keeps one cycle running at a time.
- A killed fetch stage resumes from the entries still marked with its cycle. Each fetch result is committed on its own, so a kill loses only the requests in flight.
- The worker refuses to crawl while `USER_AGENT` points at the placeholder contact URL (§14 Q5).

### 6.2 Frontier: depth limits and priority

**Depth limits:**
- Pinned domain homepages, feed items and sitemap URLs start at `internal_depth=0`.
- Pinned domains start at `external_hops=0`.
- Following an internal link: `internal_depth + 1`, same `external_hops`.
- Following an external link: `external_hops + 1`, and `internal_depth` resets to 0.
- If a URL is reached by several paths, keep the **minimum** of each field.
- Never enqueue a URL exceeding `MAX_INTERNAL_DEPTH` or `MAX_EXTERNAL_HOPS`.

**Priority (estimated PageRank, OPIC-style):** an uncrawled URL has no known outlinks, but its known in-links do have scores from the previous cycle:

```
priority(url) = Σ_{p ∈ known parents} global_pr(p) / outdegree(p)  +  λ · domain_score(url.domain)
```

- Recompute `priority` for affected frontier rows after each cycle's scoring stage (`app.crawl.frontier.recompute_priorities`). New entries start with the domain prior; an improved position keeps the higher priority.
- Pinned domains are recognized without reading `usr`: only seeds and URLs reached from them without leaving the domain have `external_hops = 0`.
- **Redirects** aren't followed inline. The target is enqueued (same position within the site, one hop further for another site) and fetched in the same cycle if due. A permanent redirect drops the old URL; a temporary one keeps it for re-crawls.
- A URL whose position improves after its links were followed doesn't re-follow them until it is re-crawled.
- The domain prior gives new pages on trusted domains a head start.
- Cold start (no scores yet): priority = the domain prior only, where pinned domains get 1.0 and everything else 0.

**Politeness:**
- Fetch and cache `robots.txt` per domain (refresh after `ROBOTS_TTL_H`, default 24), and obey it per RFC 9309.
  - Use protego: the longest match wins, and `*`/`$` patterns work. The standard library's parser does neither.
  - A 4xx means no rules. A 5xx or no answer means nothing may be fetched, unless an earlier copy is cached.
  - A disallowed URL is postponed until the next refresh and costs no budget.
- Space requests to a domain by `max(PER_DOMAIN_MIN_DELAY_S, Crawl-delay)`, measured from the previous response.
- Use conditional GET (`ETag`, `If-Modified-Since`).
- Back off exponentially on 429/5xx/timeouts.
  - Per domain: pauses double up to `DOMAIN_BACKOFF_MAX_S` (a longer `Retry-After` is honored up to the same cap). After `DOMAIN_MAX_CONSECUTIVE_ERRORS` in a row, the domain is skipped until the next cycle.
  - Per URL: retry after `FETCH_RETRY_BASE_H`, doubling. After `FETCH_MAX_FAILURES`, drop the URL.
- Enforce the content-type allowlist (HTML, PDF, RSS/Atom/XML) and `MAX_PAGE_BYTES`, counted after decompression.

### 6.3 Extraction, classification, dedup

**Type classification.** Start with heuristics, behind a `Classifier` interface:
- HTTP content-type (e.g. PDF).
- `og:type` and schema.org `@type`.
- URL patterns (`/comments/`, `/thread/`, arXiv, `.gov` publication paths).
- The feed item's own type.
- As built (`app.ingest.classify`), the classifiers are asked in this order, and the first that recognizes the page decides:
  1. URL patterns (paper repositories, `.gov` publications, thread and video sites), which win over the content type, so an arXiv PDF is a `paper`.
  2. The content type (`pdf`).
  3. `citation_title` meta tags (Google Scholar), which mark papers.
  4. schema.org types (JSON-LD and microdata), most specific first.
  5. `og:type`.
  6. A dated permalink (`/2026/08/title/`), which marks a blog `post`.
  Anything else is a `page`.
- The feed item's own type isn't used: an RSS or Atom entry carries no type beyond what the page itself says, and the frontier doesn't record which URLs came from feeds.

**Extractors.** A registry keyed by type:
- V0 set: `article` / `post` (trafilatura), generic `page` (trafilatura fallback), `pdf` (pypdf), `video` (og/schema metadata only).
- Unknown types fall back to `page`. Always record `type`.
- As built (`app.ingest.extract`): `thread` and `paper` pages use the fallback for their media type (`page` for HTML, `pdf` for PDFs) until V1 adds their own extractors.
- Metadata comes from the page's markup first (OpenGraph, `citation_*` tags, JSON-LD, `<html lang>`), then trafilatura's. A site name appended to the title (`PageRank - Wikipedia`) is dropped. The excerpt is the page's own description, else the start of its text.
- Pages that say `noindex` (robots meta tag, a meta tag for `bribot`, or an X-Robots-Tag header) don't become documents; their links are still followed unless they say `nofollow`. Links marked `rel=nofollow`, `ugc` or `sponsored` are neither followed nor graph edges. A document that turns `noindex` after it was indexed keeps its row, because deleting it would also delete users' feedback on it; hiding such documents is left to ranking (M7).
- Feeds and other XML bodies are not documents.

**URL canonicalization** (applied before insertion into `urls`; `app.crawl.urls`, built in M3 because the frontier needs it):
- Lowercase the scheme and host.
- Strip the fragment and default ports.
- Remove tracking params (`utm_*`, `fbclid`, `gclid`, `ref`, `mc_*`, a configurable list).
- Sort the remaining query params.
- Also: decode unreserved percent-escapes, uppercase the rest, and resolve dot segments. Don't upgrade http, drop the trailing slash, or lowercase the path. Reject non-http(s) URLs and URLs with credentials.
- After fetching, honor `<link rel="canonical">` and `og:url` when they are on the same site.
  - `rel=canonical` wins over `og:url`. A canonical pointing at the homepage from any other page is ignored: that misconfiguration would fold a whole site into one document.
  - The declared canonical URL is enqueued at the page's own position, like a redirect within the site.
- "Same site" (internal links, redirects, feed items) means hosts equal up to a leading `www.`.

**Dedup handler.** Implement a pipeline of `DedupStrategy` objects, each returning `(document_id | None, confidence, details)`. The first confident match wins, and every decision is written to `dedup_decisions`.
- V0 strategies:
  1. Canonical URL match.
  2. Exact hash of normalized text (lowercased, whitespace-collapsed, boilerplate stripped).
- Reserved for V1: a near-duplicate strategy (SimHash/MinHash). The interface must accept it without changes.
- As built (`app.ingest.dedup`):
  - Canonical URL match: the page's declared canonical URL (or its own URL) already maps to a document.
  - Exact hash: another document has the same normalized text; the oldest wins. Texts under `DEDUP_HASH_MIN_WORDS` never match, since short pages ("Page not found") would all merge.
  - A match counts at or above `DEDUP_MIN_CONFIDENCE`. With no match, the page becomes a new document whose canonical URL is its declared canonical.
  - A page updates its document's fields and outlinks only if it is the document's *content source*: the document's canonical URL, or a page declaring that URL while the URL itself has never been fetched. A duplicate found by hash never overwrites the original.
  - `dedup_decisions` logs every change of a URL's document, including new documents (`new`) and URLs that take their redirect target's document (`redirect`, resolved after each pass, following chains).
- Domain metadata overrides (reserved for V1) are already applied at this stage: rows whose `url_pattern` (a glob over the canonical URL) matches replace `type`, `title`, `author`, `published_at`, `language`, `excerpt`, `canonical_url` or `noindex`.

### 6.4 Embeddings and taxonomy

- **`EmbeddingProvider` interface**: `embed_documents(texts) -> list[vector]` and `embed_queries(texts, instruction) -> list[vector]` (Qwen3 prefixes the query side with a task instruction; documents are embedded as-is, once), plus model name and dimension.
  - V0 implementation: `qwen/qwen3-embedding-8b` via OpenRouter, one API key shared with the LLM. OpenRouter doesn't pass a `dimensions` parameter through, so vectors are truncated to 1024 dims client-side and L2-normalized (the model is Matryoshka-trained). Open weights mean a later self-hosted implementation produces the same vectors without re-embedding.
  - Embed `title + excerpt + first ~512 tokens` (`EMBED_TEXT_MAX_CHARS`, cut at a word boundary), in batches. The excerpt is left out when it is just the start of the text.
  - Track spend per cycle and stop embedding when `PROVIDER_MONTHLY_SPEND_CAP_USD` is reached. Un-embedded docs carry over to the next cycle.
  - **Spend metering**: every provider request goes through a `SpendMeter` holding what is left of the month's cap (calendar month, UTC). The request is estimated first (characters ÷ `PROVIDER_CHARS_PER_TOKEN` × price) and refused if it doesn't fit; afterwards it is charged the cost OpenRouter reports, else its tokens × the configured price. Charges go to `provider_spend` in the same transaction as the results, and each cycle's total to `crawl_cycles.stats`. Topic embedding is metered too; LLM calls get metered in M9.
- **Taxonomy**: adapt the **IAB Tech Lab Content Taxonomy** (latest version).
  1. Load tiers 1–2 into `topics` via a versioned seed script.
  2. IAB categories lack descriptions, so generate a one-sentence description per topic once with an LLM, commit it to the repo as a data file, and embed `name + description`.
  3. Prune ad-centric categories that make no sense for discovery. Keep the pruning list in the repo.
  4. **Before shipping, verify the taxonomy's license permits use in an open-source project.**
- **Tagging**: assign topics by cosine similarity between the document embedding and the topic embeddings. Keep the top 3 above a threshold (configurable). This is zero-shot and needs no training data.
  - As built (M5): at most `TAG_MAX_TOPICS`, each at or above `TAG_MIN_SIMILARITY` (0.2) **and** within `TAG_MAX_GAP` (0.05) of the document's best topic. Measured on the 7 saved test pages plus 15 one-topic article snippets: the right topic scored 0.24–0.44, the median topic about 0.1, and stray topics reached 0.29 ("Sports > Darts" for the PageRank article), so no absolute threshold alone separates them. The best topic was right or defensible for 19 of 22. The misses were the JPEG XL post ("Augmented Reality" ahead of "Programming"), a medicine story (weight loss or crime ahead of "Medical Health") and a climate story (space or disasters ahead of "Environment"); better topic descriptions are the lever if this matters.
  - Runs in Postgres (an exact scan over the few hundred topic vectors). `taxonomy embed` re-tags every document when topic vectors change.
  - Topics are embedded as *queries* with a category instruction (`TOPIC_EMBEDDING_INSTRUCTION`); documents stay plain passages. Measured in M2 on 18 hand-labeled article snippets: top-1 accuracy 17/18, versus 12/18 when both sides were embedded plainly (Entertainment topics then matched almost everything).

### 6.5 Scoring stage

**Global PageRank** (crawl priority):
- Build a sparse document graph from `links`, resolving `dst_url_id → document_id` and dropping unresolved edges.
- Use personalized PageRank with a personalization vector spread evenly across *all users'* pinned domains, each user weighted equally.
- Documents are graph nodes. Seeds are the documents on pinned domains, weighted evenly per domain.
- Write results to `global_scores`. Aggregate per domain into `domain_scores`.

**Per-user PPR**:
- Same graph, with the personalization vector taken from *this user's* pins.
- Power iteration with `PPR_DAMPING` / `PPR_TOL`.
- Store the top-K in `user_ppr`.
- In V0 this is a single matrix computation. V2 scaling approaches are listed in §12.

**Profile vectors** (per user):
- `interest`: weighted mean of the embeddings of the user's interest topics.
- `liked`: mean of liked document embeddings, with recency decay.
- `hidden`: mean of hidden document embeddings.

**As built (M6, `app.score`):**
- **Graph**: every document is a node. A link is an edge when its URL resolves to a document; several links between two documents make one edge, and links to the document itself none. Edges are unweighted, internal links included.
- **Power iteration**: `x ← d·W·x + (d·(mass on documents without outlinks) + 1 − d)·p`, so a surfer on a page with no outlinks jumps back to its seeds. The global surfer and every user's are the columns of one matrix, solved together (scipy.sparse), until no column changes by more than `PPR_TOL` (L1) in a step.
- **Seeds**: a user's pinned domains that have documents share the user's weight evenly, and each domain's share is split evenly among its documents. The global personalization is the mean of the users' (each user weighted equally, however many pins); with no pins at all it is uniform, i.e. plain PageRank. The global scores are therefore *not* the mean of the users' scores: pages without outlinks send each surfer back to its own seeds, which makes PageRank nonlinear in the seeds.
- **Domain scores** are the mean PageRank of the domain's documents, the expected score of a new page there, so they share the scale of the in-link term in the frontier priority (§6.2).
- **User PPR**: the top `USER_PPR_TOP_K` positive scores per user; a user whose pins have no documents yet has none.
- **Profile vectors** are stored L2-normalized. Only embeddings from the configured model count. A document counts by the user's latest like or hide of it (hiding a liked document moves it to `hidden`); a like's weight halves every `LIKED_HALF_LIFE_DAYS`. A kind with nothing to average has no row.
- Verified by hand-computed graphs (tests/test_pagerank.py), a dense linear solve on a random graph with dangling nodes, and the stage end to end against a local two-site crawl.

### 6.6 Ranking

All components are normalized to [0, 1] within the candidate set:

```
score(d, u) = w_int · interest_sim(d, u)      # cos(doc_emb, interest_vec) blended with topic-weight overlap
            + w_ppr · ppr_u(d)                 # log-scaled user PPR
            + w_fb  · feedback_sim(d, u)       # cos(doc_emb, liked_vec) − cos(doc_emb, hidden_vec), clipped
            + w_rec · recency(d)               # exp decay; half-life depends on type
            − w_hide · hide_penalty(d, u)      # similarity to hidden docs above a threshold

search(d, u, q) = score(d, u) + w_q · cos(embed(q), doc_emb)     # w_q ≫ other weights
```

- **Hard filters** (applied before scoring):
  - Documents the user hid, documents from blocked domains, and documents already clicked or liked.
  - Documents shown as impressions more than N times without a click.
  - Documents whose type is not in `content_types`.
- **Candidate generation (discovery)**, the union of:
  - top-N by `user_ppr`
  - top-N by pgvector similarity to the `interest` and `liked` vectors
  - recent documents from pinned domains
- **Candidate generation (search)**: top-N by pgvector similarity to the query embedding, then scored with `search()`.
- **Feed composition**: `(1 − ε)` of slots from the `main` slice and `ε` from exploration, where ε = `exploration_pct`. Exploration is split by `exploration_split`:
  - `adjacent_semantic`: embedding similarity to the `interest` vector in a *middle band* (similar, but not a top match), *or* tagged with sibling or parent topics of the user's interests that the user has not selected.
  - `adjacent_graph`: documents on domains within 1–2 hops of pinned domains in the domain link graph, tagged with topics **outside** the user's interests. This borrows trusted sources' taste.
  - Interleave exploration items throughout the page; don't append them at the end.
- **Diversity**: at most `MAX_PER_DOMAIN_PER_PAGE` documents from one domain per 20 results.
- **Persist** every served item to `recommendations`, with its `components` and `slice`.

### 6.7 Explanations ("why this?")

Render the top 2–3 contributing components as plain-language reasons, derived from stored `components`:
- "Linked from 3 sites you trust (e.g. example.com)": from PPR, naming the top contributing pinned domains.
- "Matches your interest: Urban Planning": from interest topics.
- "Similar to *<title>* you liked": from the nearest liked document.
- "Exploring: adjacent to *Design*": for exploration slices, stating which kind.
- Show the raw component numbers in an expandable detail view.

### 6.8 Opt-in LLM summaries

- **Off by default.** The settings page explains that when enabled, the document text and your interest tag names are sent to a model provider via OpenRouter, with no account identifiers.
- **`LLMProvider` interface**; V0 implementation: OpenRouter, with the model configurable.
- **Generation**: on demand when the user clicks "Why might I like this?" on an item. Never in bulk.
- **Prompt input**: title, excerpt, top topics and the user's interest tag names. Output: 1–2 sentences.
- **Caching and cost**: cache results in `summaries`, and count spend against the provider cap.

---

## 7. Signup survey (version 1)

The survey is multi-step and versioned. Answers are stored in `survey_responses` and then materialized into `user_interests`, `pins` and `user_settings`.

1. **Interests.** Pick from the adapted taxonomy (tier 1, expandable to tier 2) and set a weight: *interested* or *very interested*.
2. **Pinned sites.** Enter URLs or domains, or pick from a curated **suggested sources** list.
   - The list lives in the repo as a data file with name, feed URL, category and one-line description.
   - Seed it from: The Atlantic, The New York Times, The Economist, The New Yorker, Bear Blog discovery, blogroll.org, arXiv listings, and government publications (e.g. GovInfo).
   - **Verify each feed URL works before committing it.**
3. **Content types.** Choose from article/post, thread, paper, PDF, video, page.
4. **Exploration.** Choose the share of the feed for adjacent topics: 10% / 20% / 35%.
5. **Ranking preset.** Choose one:
   - *Balanced*
   - *Trust my sources* (PPR-heavy)
   - *Match my interests* (interest-heavy)
   - *Fresh* (recency-heavy)

   Each preset maps to a `weights` object.

Adding a pin enqueues the domain's homepage, feeds and sitemaps into the frontier immediately. The user can also trigger an out-of-cycle mini-crawl for new pins, capped at a small budget.

---

## 8. API (V0)

All routes are user-scoped and use Pydantic request/response models, so the OpenAPI schema is complete.

- `GET /taxonomy`
- `GET /suggested-sources`
- `POST /survey`, `GET /survey/latest`
- `GET /settings`, `PUT /settings`
- `GET /pins`, `POST /pins`, `DELETE /pins/{domain_id}`
- `GET /feed?cursor=`: returns items with `recommendation_id`, document summary, slice and reasons
- `GET /search?q=&cursor=`
- `GET /recommendations/{id}/why`: full component breakdown
- `POST /documents/{id}/summary`: returns 403 if not opted in
- `POST /feedback`: `{document_id, kind, reason_text?}`
- `POST /events`: batched impressions and clicks
- `GET /admin/cycles`, `GET /admin/metrics` (§9)

**Auth (V0):** email magic-link or a single seeded account with a session cookie. Keep it minimal, but keep `user_id` scoping real so multi-user works in V1.

---

## 9. Frontend (V0)

React + Vite + TypeScript, using only the generated orval client and hooks.

- **Survey**: multi-step flow from §7. It's shown until completed.
- **Feed**: an infinite list of cards. Each card shows title, domain, type badge, published date, excerpt, reason chips (§6.7), and actions: like, hide, block domain, "why?", and "Why might I like this?" (summaries, if opted in).
  - After a like, show an **optional** inline prompt "What did you like about it?" with free text and skip as the default.
  - Log impressions via IntersectionObserver, batched to `/events`.
- **Search**: a query box with the same card list.
- **Settings**: edit pins, interests, content types, exploration %, preset and the summaries opt-in. Re-taking the full survey can wait.
- **Admin / metrics** (for the author):
  - cycle history (pages fetched, errors, spend, duration)
  - corpus size by type
  - frontier size
  - **the success metric:** daily likes and clicks on documents from non-pinned domains
  - hide rate
  - exploration-slice click-through vs. main

---

## 10. Deployment and budget (V0)

- **Budget**: $100/month total, covering the VM, disk and provider spend.
  - Because models are hosted, the VM needs little CPU. One small always-on VM on AWS or GCP runs Postgres, the API, the worker (on a systemd timer) and the static frontend behind Caddy.
  - Price the exact instance before committing.
  - Suggested split: about $30–50 for the VM and disk, with the rest capped for providers via `PROVIDER_MONTHLY_SPEND_CAP_USD`.
- **Networking**: inbound crawl traffic is free on both clouds.
- **Storage**: expect roughly 10–20 GB per million documents (text plus embeddings). Alert at 80% disk.
- **Backups**: nightly `pg_dump` of the `usr` schema, which is small and critical. The `web` schema can be rebuilt by re-crawling, so back it up weekly.
- **Observability**: structured JSON logs, per-stage timings in `crawl_cycles.stats`, and an alert (email is fine) when a cycle fails.

---

## 11. V0 milestones

Complete each milestone with tests passing before starting the next.

| # | Milestone | Acceptance criteria |
|---|---|---|
| M0 | **Repo scaffold** | Monorepo (`backend/`, `frontend/`); Docker Compose; uv; ruff/mypy/pytest; eslint/tsc; CI running all of them; settings module; codegen script with a stale-client check in CI |
| M1 | **Schema + migrations** | Every §4 table as Alembic migrations; the two schemas; DB roles, with a test proving the crawl role cannot read `usr`; user-deletion test |
| M2 | **Taxonomy + suggested sources** | IAB seed loaded and pruned; descriptions data file; topic embeddings; license check noted in README; suggested-sources file with verified feeds |
| M3 | **Fetcher + frontier** | robots.txt, politeness, conditional GET and backoff; feed and sitemap polling; depth-limit logic with unit tests for min-path merging; OPIC priority; budget and time-limit stop; resumable after kill |
| M4 | **Extract + classify + dedup** | Classifier and extractor registries; the V0 types; rel=canonical/og:url handling (URL canonicalization and its test table landed in M3); dedup strategy pipeline plus decision log; fixtures of real saved pages for tests (`backend/tests/fixtures/pages`, redistributable pages with their licenses) |
| M5 | **Embed + tag** | `EmbeddingProvider` with an OpenRouter implementation plus a fake for tests; batching; spend tracking and cap; topic tagging; HNSW index on document embeddings |
| M6 | **Graph scoring** | Sparse graph build; global PR; domain scores; per-user PPR; profile vectors; tests against a small hand-computed graph |
| M7 | **Ranking + API** | §6.6 scoring, filters, candidates, exploration slices, diversity cap; recommendations persisted with components; §8 endpoints; explanation generation; tests for composition ratios and filters |
| M8 | **Frontend** | Survey, feed, search, settings, admin/metrics; generated client only; impression logging; optional like-reason prompt |
| M9 | **Summaries (opt-in)** | `LLMProvider` with an OpenRouter implementation plus a fake; 403 when not opted in; caching; spend counted (the LLM provider goes through the `SpendMeter`, with LLM price settings); privacy copy in settings |
| M10 | **Orchestration + deploy** | `cycle run` CLI running all stages with per-stage re-run flags; systemd timer at the user's local time; deploy docs; backups; failure alert; one full cycle run end-to-end on the real VM |

**V0 non-goals:** social features, publisher tools, manual domain submission, weight sliders, near-duplicate detection, multiple real users, local models.

---

## 12. V1: social, publishers, control

The V0 design already reserves the hooks each item uses.

- **Manual domain submission.** Users submit domains or URLs outside the survey (`pins.source = manual`). A submitted URL counts as a trust signal for its domain and is enqueued at `external_hops = 0`.
- **Ranking controls.**
  - Weight sliders on top of the presets, with a live "what changes" preview.
  - Survey re-take and versioned migration of old answers.
- **Social layer.**
  - A public **recommend** action (`feedback.visibility = public`), distinct from private likes.
  - `follows` (user → user), stored in the user store as a relations table.
  - Add user nodes to the PPR personalization: documents recommended by people you follow receive personalization mass, weighted by follow strength.
  - Profile pages listing a user's public recommendations and pins, with the user's consent.
  - Privacy controls, plus a test ensuring private signals never cross users.
- **Publisher controls.**
  - Domain verification via DNS TXT record or a `/.well-known/` file, which sets `domains.verified_owner_id`.
  - Publishers can write `domain_metadata_overrides`: opt-out of indexing, canonical corrections, type and topic corrections, a feed URL declaration.
  - **Publishers cannot boost ranking.** User control of ranking is the product's promise.
- **Dedup v2.** SimHash/MinHash near-duplicate strategy, plus an admin review page for low-confidence merges.
- **Multi-user.**
  - Real auth and per-user rate limits.
  - Per-user nightly scoring scheduled in each user's timezone.
  - Global PR seeded across all users.
- **Better extractors.**
  - Forum threads (post structure).
  - Papers (arXiv API metadata).
  - Video (transcripts where legitimately available).
- **Adaptive re-crawl.** Schedule re-crawls from observed change frequency.
- **Tuning.** Try `MAX_EXTERNAL_HOPS = 2` and compare corpus growth against the success metric.

---

## 13. V2+

- **Publisher analytics** from `events`: aggregate-only, with a minimum-count threshold before showing any number so individual users can't be identified.
- **Native social.** Threaded comments on documents; user-created links between documents as a new edge type feeding the graph.
- **Discovering unlinked content.** Candidate sources include Common Crawl, open directories and webmention/blogroll protocols (OPML). This is still an open question.
- **Content promotion and monetization.** Usage-based pricing; clearly labeled, transparent promoted content that never enters the organic ranking. This is still an open question.
- **Local models option.** Self-hosted embedding and summary implementations of the existing interfaces.
- **Scale.**
  - Monte Carlo / incremental PPR for many users.
  - Spot or preemptible batch workers for the cycle.
  - Distributed fetching.
- **Open data.** Publish shared-graph exports; the `web` schema contains no user data by construction.

---

## 14. Open questions (track and resolve)

1. Default `MAX_EXTERNAL_HOPS`: 1 vs. 2. Decide from V0 measurements.
2. IAB taxonomy license terms for an open-source project.
3. ~~Which hosted embedding model~~ **Resolved 2026-09-23:** Qwen3-Embedding-8B via OpenRouter at $0.01/1M tokens, 1024 dims. Worst case at 20k pages/night × ~700 tokens is ~420M tokens ≈ $4/month, well under the cap.
4. Whether interest tag names sent to OpenRouter are acceptable under the project's privacy promise, or should be omitted from summary prompts.
5. Project name and the bot contact page (required before the first real crawl). **Bot name resolved 2026-09-23:** `bribot`. The worker refuses to run a cycle while `USER_AGENT` points at the `example.invalid` placeholder contact URL.
