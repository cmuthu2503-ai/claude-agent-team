"""HAI-03 (FR-012) — service-token admin routes.

POST issues a token (raw shown ONCE), GET lists metadata (no secrets), DELETE
revokes. All admin-gated. Harness mirrors test_agents_route_v2 (TestClient +
real SQLiteStateStore + role_checker override for the admin gate).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import service_tokens as st_route
from src.auth.service import hash_service_token
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "st.db"))
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


def _make_app(store: Any, *, as_admin: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(st_route.router)
    app.state.state_store = store

    def _user() -> dict[str, Any]:
        return {"sub": "u1", "username": "tester", "role": "admin" if as_admin else "viewer"}

    # Override the admin gate (require_role('admin') → role_checker) for admin
    # tests; leave it in place for the unauthenticated 401 test.
    if as_admin:
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            for dep in dependant.dependencies or []:
                if getattr(dep.call, "__name__", "") == "role_checker":
                    app.dependency_overrides[dep.call] = _user
    return app


async def test_create_returns_raw_once_and_persists_hash(store):
    client = TestClient(_make_app(store, as_admin=True))
    r = client.post("/api/v1/service-tokens", json={"name": "hermes-operator", "role": "developer"})
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["name"] == "hermes-operator"
    assert data["role"] == "developer"
    raw = data["token"]
    assert raw and raw.startswith("hermes_")
    # Hash was persisted (the raw token authenticates); raw itself is never stored.
    tok = await store.get_service_token_by_hash(hash_service_token(raw))
    assert tok is not None
    assert tok.token_id == data["token_id"]


async def test_list_hides_secrets(store):
    await store.create_service_token("stok-1", "a", hash_service_token("x"), "viewer")
    client = TestClient(_make_app(store, as_admin=True))
    r = client.get("/api/v1/service-tokens")
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) == 1
    row = items[0]
    assert row["token_id"] == "stok-1"
    assert "token" not in row and "hashed_token" not in row
    assert row["revoked"] is False


async def test_revoke_204_then_404(store):
    await store.create_service_token("stok-r", "r", hash_service_token("r"), "admin")
    client = TestClient(_make_app(store, as_admin=True))
    assert client.delete("/api/v1/service-tokens/stok-r").status_code == 204
    # Already revoked → 404 (revoke is idempotent at the store layer).
    assert client.delete("/api/v1/service-tokens/stok-r").status_code == 404
    # Never existed → 404.
    assert client.delete("/api/v1/service-tokens/stok-missing").status_code == 404


async def test_create_invalid_role_400(store):
    client = TestClient(_make_app(store, as_admin=True))
    r = client.post("/api/v1/service-tokens", json={"name": "x", "role": "superuser"})
    assert r.status_code == 400


async def test_create_blank_name_400(store):
    client = TestClient(_make_app(store, as_admin=True))
    r = client.post("/api/v1/service-tokens", json={"name": "   ", "role": "viewer"})
    assert r.status_code == 400


async def test_admin_gate_blocks_unauthenticated(store):
    # No admin override + no token → require_role('admin') rejects (not open).
    client = TestClient(_make_app(store, as_admin=False))
    r = client.post("/api/v1/service-tokens", json={"name": "x", "role": "viewer"})
    assert r.status_code in (401, 403)


async def test_whoami_echoes_role(store):
    # GET /me authenticates with the service token itself (not admin) and echoes
    # the resolved role — the agent-team-mcp server uses this to learn its role.
    raw = "whoami-token"
    await store.create_service_token("stok-me", "hermes-monitor", hash_service_token(raw), "developer")
    client = TestClient(_make_app(store, as_admin=False))
    r = client.get("/api/v1/service-tokens/me", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["token_id"] == "stok-me"
    assert data["role"] == "developer"
    assert data["is_service_token"] is True


async def test_whoami_rejects_no_token(store):
    client = TestClient(_make_app(store, as_admin=False))
    assert client.get("/api/v1/service-tokens/me").status_code == 401
