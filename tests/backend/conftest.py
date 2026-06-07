"""Shared fixtures for backend tests.

Provides:
  * `client`         — TestClient bound to the CrewAI app (fresh per test
                       so middleware/env mutations don't leak)
  * `temp_db`        — points CREWAI_DB_PATH at a fresh sqlite file in
                       tmp_path; initialises it so `SELECT 1` succeeds
  * `clean_llm_env`  — removes both LLM API keys so /ready check can be
                       exercised in failure mode; tests that want green
                       /ready should monkeypatch keys in themselves
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create a usable sqlite DB file and point CREWAI_DB_PATH at it."""
    db_path = tmp_path / "crewai.db"
    # Create + close so the file exists and SELECT 1 works.
    conn = sqlite3.connect(str(db_path))
    conn.close()
    monkeypatch.setenv("CREWAI_DB_PATH", str(db_path))
    return db_path


@pytest.fixture
def clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure neither LLM API key is set."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def client(temp_db: Path) -> Iterator[TestClient]:
    """A TestClient bound to a freshly built CrewAI app.

    Builds via `create_app()` so each test gets isolated middleware state
    and any monkeypatched env vars (CORS, CREWAI_DB_PATH) take effect.
    """
    # Import lazily so env-var monkeypatches applied via fixtures take
    # effect before the app reads them.
    from src.api.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
