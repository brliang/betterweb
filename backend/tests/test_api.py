from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from scripts.export_openapi import render_schema

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_committed_openapi_schema_is_current() -> None:
    committed = (REPO_ROOT / "openapi.json").read_text()
    assert committed == render_schema(), "openapi.json is stale; run `make codegen`"
