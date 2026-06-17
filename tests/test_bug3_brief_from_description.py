"""BUG-3 — a project created with a `description` (and no separate brief) must be
able to generate a PRD. Verifies the description→brief seeding without invoking
the LLM (we assert generate_prd gets PAST the brief 400 to the executor-503).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import projects as projects_route
from src.api.routes.projects import _seed_brief_from_description
from src.auth.service import get_current_user
from src.models.base import ArtifactKind, Project, ProjectStatus
from src.state.sqlite_store import SQLiteStateStore

_DESC = "An AI agent team platform with engineering specialists and a Kanban board."  # ≥50


async def _store(tmp: str) -> SQLiteStateStore:
    s = SQLiteStateStore(db_path=str(Path(tmp) / "bug3.db"))
    await s.initialize()
    return s


# ── helper (unit) ────────────────────────────────────────────────────────────

async def test_seed_brief_from_description_creates_brief():
    with tempfile.TemporaryDirectory() as tmp:
        s = await _store(tmp)
        try:
            await s.create_project(Project(project_id="proj-1", name="Atlas",
                                           status=ProjectStatus.ACTIVE, description=_DESC))
            project = await s.get_project("proj-1")
            art = await _seed_brief_from_description(s, project, "u1")
            assert art is not None
            assert art.content == _DESC
            stored = await s.get_artifact("proj-1", ArtifactKind.BRIEF)
            assert stored is not None and stored.content == _DESC
        finally:
            await s.close()


async def test_seed_returns_none_for_short_or_missing_description():
    with tempfile.TemporaryDirectory() as tmp:
        s = await _store(tmp)
        try:
            await s.create_project(Project(project_id="p2", name="Short",
                                           status=ProjectStatus.ACTIVE, description="too short"))
            project = await s.get_project("p2")
            assert await _seed_brief_from_description(s, project, "u1") is None
        finally:
            await s.close()


# ── route (integration, no LLM) ──────────────────────────────────────────────

def _app(store) -> FastAPI:
    app = FastAPI()
    app.include_router(projects_route.router)
    app.state.state_store = store
    # NOTE: deliberately NO app.state.agent_executor → generate_prd 503s AFTER the
    # brief check, so a non-400 proves the brief precondition passed.
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u1", "user_id": "u1",
                                                          "username": "alice", "role": "admin"}
    return app


async def test_generate_prd_passes_brief_check_with_only_description():
    with tempfile.TemporaryDirectory() as tmp:
        s = await _store(tmp)
        try:
            await s.create_project(Project(project_id="proj-1", name="Atlas",
                                           status=ProjectStatus.ACTIVE, description=_DESC))
            client = TestClient(_app(s))
            r = client.post("/api/v1/projects/proj-1/prd/generate")
            # Brief check passed (seeded from description) → reaches executor-503, NOT 400.
            assert r.status_code != 400, r.text
            assert r.status_code == 503  # executor not configured in this test
            # and the brief was actually seeded from the description
            brief = await s.get_artifact("proj-1", ArtifactKind.BRIEF)
            assert brief is not None and brief.content == _DESC
        finally:
            await s.close()


async def test_generate_prd_still_400_without_brief_or_description():
    with tempfile.TemporaryDirectory() as tmp:
        s = await _store(tmp)
        try:
            await s.create_project(Project(project_id="p3", name="Bare",
                                           status=ProjectStatus.ACTIVE, description=""))
            client = TestClient(_app(s))
            r = client.post("/api/v1/projects/p3/prd/generate")
            assert r.status_code == 400
            assert "≥50" in r.json()["detail"] or "50" in r.json()["detail"]
        finally:
            await s.close()
