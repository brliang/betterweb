# Discovery Engine

A graph-centric, open-source content discovery and search engine. Pin sites you trust; the
system crawls outward from them, builds a shared web graph, and ranks content with transparent,
user-controllable heuristics. Every recommendation explains why it was shown.

The full design and milestone list is in [docs/PLAN.md](docs/PLAN.md).

## Layout

| Path | What |
|---|---|
| `backend/` | Python 3.12 · FastAPI · uv. `app/` is the API; `app/worker` is the nightly crawl CLI. |
| `backend/app/db/` | SQLAlchemy models for the `web` (shared graph) and `usr` (user store) schemas |
| `backend/migrations/` | Alembic migrations, including the DB roles that keep the schemas apart |
| `backend/app/providers/` | Embedding and LLM providers (OpenRouter), behind interfaces |
| `backend/data/taxonomy/` | The vendored IAB taxonomy, our adaptation of it, and topic descriptions |
| `backend/data/suggested_sources.toml` | Curated sites offered at signup, with verified feeds |
| `frontend/` | React 19 · TypeScript · Vite · TanStack Query · Tailwind CSS v4 |
| `openapi.json` | Exported API schema (generated, committed) |
| `frontend/src/api/generated/` | Typed client + hooks from orval (generated, committed) |

## Prerequisites

[uv](https://docs.astral.sh/uv/) (installs Python 3.12 itself), Node 24 (see `frontend/.nvmrc`),
and Docker (on macOS, [OrbStack](https://orbstack.dev) works well).

## Common commands

```sh
make install        # backend + frontend dependencies
docker compose up -d db   # Postgres + pgvector; backend tests need it
make migrate        # apply migrations to DATABASE_URL
make migration name="add foo"   # autogenerate the next migration from model changes
make taxonomy       # load topics into the DB and embed them (needs OPENROUTER_API_KEY)
make verify-sources # check suggested sources' robots.txt and feeds
make check          # ruff, mypy, pytest, prettier, eslint, tsc
make format         # auto-fix formatting (ruff, prettier)
make codegen        # re-export openapi.json and regenerate the TS client
make dev-api        # FastAPI on :8000
make dev-web        # Vite on :5173, proxying /api -> :8000
docker compose up   # Postgres (pgvector), migrations, API, frontend
docker compose run --rm worker cycle run
```

**Typed client rule:** never hand-write API types in the frontend. After changing any backend
route or model, run `make codegen` and commit the result; CI fails if the client is stale.

Coding conventions and best practices for contributors (human or AI) are in
[CLAUDE.md](CLAUDE.md).

## Configuration

Every tunable lives in `backend/app/settings.py` and can be overridden with an environment
variable of the same upper-case name (see `.env.example`).

Embeddings (Qwen3-Embedding-8B, 1024 dimensions) and LLM calls go through
[OpenRouter](https://openrouter.ai) with one `OPENROUTER_API_KEY`. Set a monthly credit limit
on the key in OpenRouter as well; the app's own cap is `PROVIDER_MONTHLY_SPEND_CAP_USD`.

## Topic taxonomy

Topics are adapted from the [IAB Tech Lab Content Taxonomy 3.1](https://github.com/InteractiveAdvertisingBureau/Taxonomies)
(`backend/data/taxonomy/`). `adaptation.toml` lists what we prune (ad-targeting and
brand-safety categories), promote and add; `descriptions.json` holds one generated sentence
per topic, written by `python -m scripts.describe_topics` and reviewed before committing.

**License:** IAB Tech Lab's repository states its taxonomies are under the
[Creative Commons Attribution 3.0](https://creativecommons.org/licenses/by/3.0/) license,
which requires attribution. Whether that suits this project's eventual open-source license is
not yet confirmed (docs/PLAN.md §14 Q2).
