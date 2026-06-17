"""BUG-5 — lifecycle read endpoints must accept a SERVICE token (get_principal),
not just a human JWT (get_current_user).

Repro/verify: override get_current_user to REJECT (as if a service token presented
to a human-only route → 401) and get_principal to a valid service principal. A
read that returns anything other than 401 proves it depends on get_principal.
Before the fix these used get_current_user and returned
`401: Invalid token: Not enough segments`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.routes import projects as projects_route
from src.auth.service import get_current_user, get_principal
from src.models.base import Project, ProjectStatus
from src.state.sqlite_store import SQLiteStateStore

_SERVICE_PRINCIPAL = {
    "sub": "stok-1", "token_id": "stok-1", "username": "hermes",
    "role": "developer", "is_service_token": True,
}


@pytest.fixture
async def client_and_pid():
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteStateStore(db_path=str(Path(tmp) / "bug5.db"))
        await store.initialize()
        await store.create_project(
            Project(project_id="proj-1", name="Atlas", status=ProjectStatus.ACTIVE)
        )
        app = FastAPI()
        app.include_router(projects_route.router)
        app.state.state_store = store

        # Service token: passes get_principal, but human-only auth rejects it.
        app.dependency_overrides[get_principal] = lambda: dict(_SERVICE_PRINCIPAL)

        def _human_only():
            raise HTTPException(status_code=401, detail="Invalid token: Not enough segments")

        app.dependency_overrides[get_current_user] = _human_only
        try:
            yield TestClient(app), "proj-1"
        finally:
            await store.close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/proj-1/prd",
        "/api/v1/projects/proj-1/api-spec",
        "/api/v1/projects/proj-1/tasks",
        "/api/v1/projects/proj-1/build-plan/rollup",
        "/api/v1/projects/proj-1/brief",
        "/api/v1/projects/proj-1/epics",      # BUG-7
        "/api/v1/projects/proj-1/features",   # BUG-7
    ],
)
async def test_lifecycle_reads_accept_service_token(client_and_pid, path):
    client, _pid = client_and_pid
    r = client.get(path)
    # The point: NOT 401. (200 = data, 404 = no artifact yet — both mean auth passed.)
    assert r.status_code != 401, f"{path} rejected the service token (still human-only auth)"
    assert r.status_code in (200, 404), f"{path} unexpected status {r.status_code}: {r.text}"


async def test_control_unknown_project_is_404_not_401(client_and_pid):
    # Sanity: auth passes (service token) and a missing project is a clean 404,
    # never the JWT-decode 401 we were getting.
    client, _pid = client_and_pid
    r = client.get("/api/v1/projects/ghost/prd")
    assert r.status_code == 404
