.PHONY: install codegen check-codegen migrate migration format lint typecheck test check dev-api dev-web

GENERATED := openapi.json frontend/src/api/generated

install:
	cd backend && uv sync --locked
	cd frontend && npm ci

# Export the OpenAPI schema from FastAPI, then regenerate the typed TS client + hooks.
codegen:
	cd backend && uv run python -m scripts.export_openapi ../openapi.json
	cd frontend && npm run codegen

check-codegen: codegen
	@git diff --exit-code -- $(GENERATED) && \
	test -z "$$(git ls-files --others --exclude-standard -- $(GENERATED))" || \
	(echo "Generated client is stale: run 'make codegen' and commit the result." && exit 1)

# Apply migrations to DATABASE_URL (default: the Compose db on localhost).
migrate:
	cd backend && uv run alembic upgrade head

# Autogenerate the next migration from model changes: make migration name="add foo to bar"
# Review the generated file by hand before committing (see CLAUDE.md).
migration:
	@test -n "$(name)" || (echo 'usage: make migration name="describe the change"' && exit 1)
	cd backend && uv run alembic revision --autogenerate -m "$(name)" \
		--rev-id $$(printf '%04d' $$(( $$(ls migrations/versions | grep -cE '^[0-9]{4}_') + 1 )))

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .
	cd frontend && npm run format

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run format:check && npm run lint

typecheck:
	cd backend && uv run mypy
	cd frontend && npm run typecheck

# Tests in backend/tests/db need Postgres: `docker compose up -d db`.
test:
	cd backend && uv run pytest

check: lint typecheck test

dev-api:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev
