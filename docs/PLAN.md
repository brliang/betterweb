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
| Embeddings | Hosted via HuggingFace Inference (behind `EmbeddingProvider` interface) |
| LLM summaries | Hosted via OpenRouter (behind `LLMProvider` interface) |
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
  - Fields: `id`, `host` (normalized, unique), `status`, `robots_txt`, `robots_fetched_at`, `crawl_delay_s`, `feed_urls text[]`, `sitemap_urls text[]`, `first_seen_at`, `last_crawled_at`.
  - Reserved for V1: `verified_owner_id` (nullable, no FK yet).
- **`urls`**
  - Fields: `id`, `url` (canonicalized, unique), `domain_id`, `document_id` (nullable until deduped), `http_status`, `etag`, `last_modified`, `content_hash`, `first_seen_at`, `last_fetched_at`, `fetch_count`, `change_count`.
- **`documents`**
  - Fields: `id`, `canonical_url_id`, `domain_id`, `type` (enum), `title`, `author`, `published_at`, `language`, `text` (extracted, normalized), `excerpt`, `word_count`, `content_hash`, `created_at`, `updated_at`.
  - Reserved for V1: `simhash bigint` (nullable).
- **`links`**
  - Fields: `src_document_id`, `dst_url_id`, `anchor_text`, `is_internal`.
  - Edges point to **URLs**, not Documents, because the destination may not be crawled yet. Resolve through `urls.document_id` when building the graph.
- **`document_embeddings`**: `document_id`, `model`, `vector vector(N)`, `created_at`. Store the model name so a model change triggers re-embedding.
- **`topics`**
  - Fields: `id`, `external_id` (IAB ID), `name`, `parent_id`, `tier`, `description`, `embedding vector(N)`.
  - Holds the adapted taxonomy (§6.4).
- **`document_topics`**: `document_id`, `topic_id`, `score`.
- **`frontier`**
  - Fields: `url_id` (unique), `internal_depth`, `external_hops`, `priority`, `reason` (`new` | `recrawl`), `next_fetch_at`, `enqueued_at`.
- **`dedup_decisions`**
  - Fields: `id`, `url_id`, `document_id`, `method`, `confidence`, `details jsonb`, `created_at`.
  - Append-only log of every URL→Document merge, so strategies can be audited and tuned.
- **`crawl_cycles`**
  - Fields: `id`, `started_at`, `finished_at`, `status`, `page_budget`, `pages_fetched`, `stats jsonb` (per-stage counts, errors, provider spend).
- **`global_scores`**: `document_id`, `cycle_id`, `pagerank`. PageRank seeded with *all* users' pins; drives crawl priority.
- **`domain_scores`**: `domain_id`, `cycle_id`, `score`. Used as the domain prior.
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
| `PPR_TOL` / `PPR_MAX_ITER` | 1e-6 / 100 | |
| `EXPLORATION_PCT` | 0.20 | Overridden by survey answer |
| `EXPLORATION_SPLIT` | 50/50 semantic/graph | |
| `RECENCY_HALF_LIFE_DAYS` | 7 | Longer for evergreen types (paper, PDF) |
| `MAX_PER_DOMAIN_PER_PAGE` | 2 | Diversity cap per 20 results |
| `PROVIDER_MONTHLY_SPEND_CAP_USD` | 40 | Hard stop on embedding + LLM spend |
| `USER_AGENT` | `<name>Bot/0.1 (+https://<project-url>/bot)` | Contact page required |

---

## 6. Core algorithms

### 6.1 Crawl cycle

A **crawl cycle** is one nightly batch run with a fixed page budget, recorded in `crawl_cycles`. Stages run in order. Each stage is idempotent and resumable (safe to re-run after a crash) and records its stats.

1. **Fetch**
   1. Poll all feeds and sitemaps for domains in the frontier's reach, and enqueue new items.
   2. Pop URLs from `frontier` in `priority` order until the page budget or time limit is reached.
      - Reserve `CYCLE_RECRAWL_SHARE` of the budget for `recrawl` entries.
      - Prioritize re-crawls by `change_count / fetch_count`.
   3. Unfetched entries stay in the frontier for the next cycle.
2. **Extract/Classify**: determine the document type and extract text and metadata (§6.3).
3. **Dedup**: map each URL to a Document (§6.3).
4. **Embed + Tag**: embed new or changed documents, then assign topics (§6.4).
5. **Scores**: compute global PageRank, domain scores and per-user PPR, then refresh user profile vectors (§6.5).

Use Postgres as the work queue (`SELECT … FOR UPDATE SKIP LOCKED`) rather than adding a queue service.

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

- Recompute `priority` for affected frontier rows after each cycle's scoring stage.
- The domain prior gives new pages on trusted domains a head start.
- Cold start (no scores yet): priority = the domain prior only, where pinned domains get 1.0 and everything else 0.

**Politeness:**
- Fetch and cache `robots.txt` per domain (refresh daily), and obey it.
- Use conditional GET (`ETag`, `If-Modified-Since`).
- Back off exponentially on 429/5xx.
- Enforce the content-type allowlist: HTML, PDF, RSS/Atom/XML.

### 6.3 Extraction, classification, dedup

**Type classification.** Start with heuristics, behind a `Classifier` interface:
- HTTP content-type (e.g. PDF).
- `og:type` and schema.org `@type`.
- URL patterns (`/comments/`, `/thread/`, arXiv, `.gov` publication paths).
- The feed item's own type.

**Extractors.** A registry keyed by type:
- V0 set: `article` / `post` (trafilatura), generic `page` (trafilatura fallback), `pdf` (pypdf), `video` (og/schema metadata only).
- Unknown types fall back to `page`. Always record `type`.

**URL canonicalization** (applied before insertion into `urls`):
- Lowercase the scheme and host.
- Strip the fragment and default ports.
- Remove tracking params (`utm_*`, `fbclid`, `gclid`, `ref`, `mc_*`, a configurable list).
- Sort the remaining query params.
- After fetching, honor `<link rel="canonical">` and `og:url` when they are on the same site.

**Dedup handler.** Implement a pipeline of `DedupStrategy` objects, each returning `(document_id | None, confidence, details)`. The first confident match wins, and every decision is written to `dedup_decisions`.
- V0 strategies:
  1. Canonical URL match.
  2. Exact hash of normalized text (lowercased, whitespace-collapsed, boilerplate stripped).
- Reserved for V1: a near-duplicate strategy (SimHash/MinHash). The interface must accept it without changes.

### 6.4 Embeddings and taxonomy

- **`EmbeddingProvider` interface**: `embed(texts: list[str]) -> list[vector]`, plus model name and dimension.
  - V0 implementation: HuggingFace Inference.
  - Embed `title + excerpt + first ~512 tokens`, in batches.
  - Track spend per cycle and stop embedding when `PROVIDER_MONTHLY_SPEND_CAP_USD` is reached. Un-embedded docs carry over to the next cycle.
- **Taxonomy**: adapt the **IAB Tech Lab Content Taxonomy** (latest version).
  1. Load tiers 1–2 into `topics` via a versioned seed script.
  2. IAB categories lack descriptions, so generate a one-sentence description per topic once with an LLM, commit it to the repo as a data file, and embed `name + description`.
  3. Prune ad-centric categories that make no sense for discovery. Keep the pruning list in the repo.
  4. **Before shipping, verify the taxonomy's license permits use in an open-source project.**
- **Tagging**: assign topics by cosine similarity between the document embedding and the topic embeddings. Keep the top 3 above a threshold (configurable). This is zero-shot and needs no training data.

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
| M4 | **Extract + classify + dedup** | Classifier and extractor registries; the V0 types; canonicalization with a thorough unit-test table; dedup strategy pipeline plus decision log; fixtures of real saved pages for tests |
| M5 | **Embed + tag** | `EmbeddingProvider` with an HF implementation plus a fake for tests; batching; spend tracking and cap; topic tagging |
| M6 | **Graph scoring** | Sparse graph build; global PR; domain scores; per-user PPR; profile vectors; tests against a small hand-computed graph |
| M7 | **Ranking + API** | §6.6 scoring, filters, candidates, exploration slices, diversity cap; recommendations persisted with components; §8 endpoints; explanation generation; tests for composition ratios and filters |
| M8 | **Frontend** | Survey, feed, search, settings, admin/metrics; generated client only; impression logging; optional like-reason prompt |
| M9 | **Summaries (opt-in)** | `LLMProvider` with an OpenRouter implementation plus a fake; 403 when not opted in; caching; spend counted; privacy copy in settings |
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
3. Which hosted embedding model; confirm its price per 1M tokens fits the cap at the chosen page budget.
4. Whether interest tag names sent to OpenRouter are acceptable under the project's privacy promise, or should be omitted from summary prompts.
5. Project name, bot user agent, and the bot contact page (required before the first real crawl).
