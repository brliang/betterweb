.PHONY: install codegen check-codegen lint typecheck test check dev-api dev-web

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

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

typecheck:
	cd backend && uv run mypy
	cd frontend && npm run typecheck

test:
	cd backend && uv run pytest

check: lint typecheck test

dev-api:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev
