# Discovery Engine

`docs/PLAN.md` is the source of truth. Work through V0 milestones (§11) in order, each with
tests passing before the next. Don't build V1/V2 features, but honor every "Reserved for V1" note.

## Commands
`make check` (all format/lint/type/test), `make format`, `make codegen`, `make migrate`,
`make migration name="..."`, `make taxonomy`, `make verify-sources`, `make dev-api`,
`make dev-web`, `docker compose up -d`. Backend commands run via `uv run` from `backend/`;
frontend via `npm run` from `frontend/`. Run `make check` before every commit; CI runs the
same checks.

## Project rules
- No magic numbers: thresholds, weights, limits and budgets go in `backend/app/settings.py`,
  each with a one-line attribute docstring.
- `web` schema tables must never reference `usr`. User-scoped tables and queries key on `user_id`.
- Frontend uses only the orval-generated client. After any backend route/model change run
  `make codegen` and commit `openapi.json` + `frontend/src/api/generated/` with it.
- Model providers (embeddings, LLM) sit behind the `EmbeddingProvider` / `LLMProvider`
  protocols in `app/providers/`, with fakes in `tests/fakes.py`; tests never call a real
  provider. Both use OpenRouter with one `OPENROUTER_API_KEY`. Never send user identifiers to a
  provider. Every provider request goes through a `SpendMeter` (`app/providers/spend.py`),
  which refuses it past `PROVIDER_MONTHLY_SPEND_CAP_USD`; record its charges with
  `app.spend.record_spend` in the transaction that stores what they paid for.
- Crawling (`app/crawl/`) is polite by construction: every request, polls and redirects
  included, goes through its domain's robots.txt check and `DomainGate`; never add a request
  path that skips them, and never follow redirects inline. Tests use `tests/fake_web.py`, never
  the network. URLs are stored only in `app.crawl.urls.canonicalize` form.
- Extraction (`app/ingest/`) honors `noindex`/`nofollow` (robots meta tags and X-Robots-Tag)
  and rel=nofollow/ugc/sponsored links. New classifiers, extractors and dedup strategies plug
  into the registries there. Test pages in `tests/fixtures/pages/` are real, redistributable
  pages saved with `scripts.capture_page`; record each one's license in the manifest.
- Repo data lives in `backend/data/`: the vendored IAB file (never edit it; change
  `adaptation.toml`), generated `descriptions.json` (rerun `scripts.describe_topics` after any
  adaptation change; a test fails when it is stale), and `suggested_sources.toml` (run
  `make verify-sources` after edits; a site whose robots.txt refuses us is dropped, never
  worked around).

## Python / FastAPI
- Python 3.12, fully typed; mypy `--strict` must pass. No `Any` or `# type: ignore` without a
  comment explaining why.
- Absolute imports only (`from app.x import y`); ruff enforces this.
- Timezone-aware datetimes only: `datetime.now(UTC)`, store `timestamptz`. Ruff `DTZ` enforces this.
- Use `logging` (module-level `logger = logging.getLogger(__name__)`), never `print`.
- Secrets are `SecretStr` in settings; read with `.get_secret_value()` only where needed.
- Routes live in `app/api/<area>.py` as an `APIRouter`, included in `create_app()`.
- Every request and response is a Pydantic model; return the model and let the return
  annotation define the response schema (no `response_model=` duplication).
- Dependencies use `Annotated[T, Depends(...)]` (ruff `FAST002`). Get settings via
  `Depends(get_settings)` in routes so tests can override them.
- `async def` handlers must never call blocking I/O (sync DB drivers, `requests`, file reads);
  use async libraries or a plain `def` handler. HTTP client: `httpx2` (async).
- Tests: pytest, plain functions and fixtures, `parametrize` for tables of cases. Warnings are
  errors; ignore a specific upstream warning in `pyproject.toml` with a comment saying why.

## Database
- Models live in `app/db/web.py` and `app/db/usr.py`: SQLAlchemy 2.x typed ORM (`Mapped[...]`,
  `mapped_column`), `__table_args__` naming the schema. `str` maps to `text`, `datetime` to
  `timestamptz`; enums are `StrEnum`s in `app/enums.py`, stored as text with a CHECK.
- Every `usr` table has `user_id` (`user_fk()`) cascading from `usr.users`, so
  `app.users.delete_user` is one statement. `web` tables never reference `usr`. Tests enforce both.
- DB access is async (`AsyncSession`, psycopg 3). Alembic runs sync with the same URL.
- Every schema change is a migration: `make migration name="..."` autogenerates the next
  numbered file. Review it by hand (autogenerate misses schemas, extensions, grants, and
  `use_alter` foreign keys); never edit a merged migration; every migration must downgrade.
  Tests fail if models and migrations disagree, or if a downgrade leaves anything behind.
- Roles (migration 0002): `discovery_crawl` (web only, no `usr` at all), `discovery_score`
  (+ read `usr`, write `user_ppr`/`user_profile_vectors`), `discovery_api` (+ read/write `usr`).
  A new `usr` table the scoring stage writes needs an explicit grant; `tests/db/test_roles.py`
  holds the policy.
- Tests that touch Postgres go in `tests/db/` (auto-marked `db`) and run against the Compose
  `db` service or CI's service container, never SQLite. Each test's transaction is rolled back.

## React / TypeScript
- Function components only. React Compiler is on: don't write `useMemo`, `useCallback` or
  `memo` by hand.
- Avoid `useEffect`. Server data comes from the generated TanStack Query hooks; derived values
  are computed during render; responses to user actions go in event handlers. An effect is only
  for syncing with something outside React (IntersectionObserver, a DOM API, a subscription),
  and must clean up after itself. Never call `setState` synchronously in an effect (lint error).
- Mutations use the generated mutation hooks and invalidate affected queries with the
  generated `get…QueryKey()` helpers; never hand-write query keys for API data.
- Keep server state in TanStack Query and UI state in local `useState`; no global store unless
  a real need appears.
- API errors arrive as `ApiError` (`src/api/fetcher.ts`) with `status` and `body`; handle
  `isPending` / `isError` explicitly in every component that queries.
- TypeScript is strict with `noUncheckedIndexedAccess`. No `any`, no non-null `!`, no `as`
  casts outside `src/api/fetcher.ts`.
- One component per file, named export matching the filename, except `App.tsx`.

## Styling / accessibility
- Tailwind CSS v4 utility classes only; no new CSS files or inline `style` objects.
  `src/index.css` holds only `@import "tailwindcss"` and any `@theme` tokens.
- Prettier sorts Tailwind classes; run `make format` rather than ordering them by hand.
- Support light and dark mode (`dark:` variants) from the start.
- Semantic HTML: real `<button>`s for actions, `<a>` for navigation, labels on every input,
  visible focus states, and alt text on images. (`eslint-plugin-jsx-a11y` isn't enabled yet
  because it doesn't support ESLint 10; follow these rules by hand until it does.)

## Git / CI
- Small commits, imperative subject lines, one milestone or fix per PR.
- CI must be green: backend (ruff, mypy, pytest), frontend (prettier, eslint, tsc, build),
  stale-codegen check, and the backend Docker build.
- Dependabot opens grouped weekly dependency PRs; let CI pass before merging.

## Status
- M0 (repo scaffold): done. Local Docker is OrbStack; `docker compose up -d` verified
  (Postgres 16.15 + pgvector 0.8.6).
- M1 (schema + migrations): done. Login users for the three roles are created per deployment
  (M3 wires the crawl stages to theirs).
- M2 (taxonomy + suggested sources): done. Embedding columns are `vector(1024)`
  (`EMBEDDING_DIMENSIONS`; migration 0003); M5 adds the HNSW indexes. Topics are embedded as
  instructed queries against plain document embeddings (PLAN.md §6.4). The taxonomy license
  question (§14 Q2) is deferred by the user.
- M3 (fetcher + frontier): done. `cycle run` runs the fetch stage (resumable; see PLAN.md
  §6.1-6.2) and refuses the placeholder `USER_AGENT`. Fetched bodies wait in `web.raw_pages`
  for M4's extract stage. URL canonicalization moved into M3; M4 adds rel=canonical.
- M4 (extract + classify + dedup): done. `cycle run` runs fetch then extract; the extract
  stage also follows links (so the crawl reaches one link level further per cycle) and
  dedups. The bot is named `bribot`; its contact page (§14 Q5) is still open.
- M5 (embed + tag): done. `cycle run` now also runs the embed stage (needs
  `OPENROUTER_API_KEY`); spend is metered against the monthly cap and logged in
  `web.provider_spend`. Tagging keeps topics within `TAG_MAX_GAP` of a document's best one
  above `TAG_MIN_SIMILARITY` (measured; see PLAN.md §6.4). LLM calls get metered in M9.
- M6 (graph scoring): done. `cycle run` ends with the scoring stage (`app/score/`): one power
  iteration solves the global and every user's personalized PageRank (numpy/scipy), then
  global/domain scores, `user_ppr`, frontier priorities and profile vectors are replaced in one
  transaction. It is the only cycle stage that reads `usr`; a test runs it as `discovery_score`.
