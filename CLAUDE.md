# Discovery Engine

`docs/PLAN.md` is the source of truth. Work through V0 milestones (§11) in order, each with
tests passing before the next. Don't build V1/V2 features, but honor every "Reserved for V1" note.

## Conventions
- No magic numbers: thresholds, weights, limits and budgets go in `backend/app/settings.py`.
- `web` schema tables must never reference `usr`. User-scoped tables and queries key on `user_id`.
- Frontend uses only the orval-generated client. After backend API changes run `make codegen`.
- Model providers (embeddings, LLM) sit behind interfaces with a fake implementation for tests.

## Commands
`make check` (all lint/type/test), `make codegen`, `make dev-api`, `make dev-web`.
Backend commands run via `uv run` from `backend/`; frontend via `npm run` from `frontend/`.

## Status
- M0 (repo scaffold): done. Docker Compose file is written but not yet run locally (no Docker on the dev machine).
