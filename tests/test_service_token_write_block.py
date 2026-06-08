"""HAI-51 (FR-015a) — service-token write-block middleware.

A live SERVICE token may only POST /api/v1/proposals; every other state-changing
call is 403, regardless of route guard. Human (JWT) and read traffic untouched.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.service_token_write_block import ServiceTokenWriteBlockMiddleware
from src.auth.service import hash_service_token
from src.state.sqlite_store import SQLiteStateStore

SERVICE_RAW = "hermes_secret_token_value"


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "wb.db"))
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


def _app(store) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ServiceTokenWriteBlockMiddleware)
    app.state.state_store = store

    @app.post("/api/v1/projects")
    async def _create_project():
        return {"ok": "created"}

    @app.delete("/api/v1/projects/{pid}")
    async def _delete_project(pid: str):
        return {"ok": "deleted"}

    @app.post("/api/v1/proposals")
    async def _create_proposal():
        return {"ok": "proposed"}

    @app.post("/api/v1/proposals/{pid}/confirm")
    async def _confirm(pid: str):
        return {"ok": "confirmed"}

    @app.get("/api/v1/requests")
    async def _list_requests():
        return {"ok": "listed"}

    return app


async def _seed(store, *, revoked: bool = False, raw: str = SERVICE_RAW):
    await store.create_service_token("stok-1", "hermes", hash_service_token(raw), "developer")
    if revoked:
        await store.revoke_service_token("stok-1")


def _svc():
    return {"Authorization": f"Bearer {SERVICE_RAW}"}


async def test_blocked_on_write(store):
    await _seed(store)
    r = TestClient(_app(store)).post("/api/v1/projects", headers=_svc())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "service_token_write_forbidden"


async def test_blocked_on_delete(store):
    await _seed(store)
    r = TestClient(_app(store)).delete("/api/v1/projects/p1", headers=_svc())
    assert r.status_code == 403


async def test_blocked_on_proposal_confirm(store):
    # confirm/reject are human-only (FR-038) — service token blocked there too.
    await _seed(store)
    r = TestClient(_app(store)).post("/api/v1/proposals/p1/confirm", headers=_svc())
    assert r.status_code == 403


async def test_allowed_to_create_proposal(store):
    await _seed(store)
    r = TestClient(_app(store)).post("/api/v1/proposals", headers=_svc())
    assert r.status_code == 200
    assert r.json()["ok"] == "proposed"


async def test_allowed_to_read(store):
    await _seed(store)
    r = TestClient(_app(store)).get("/api/v1/requests", headers=_svc())
    assert r.status_code == 200


async def test_human_jwt_not_blocked(store):
    # A non-service bearer (JWT) hashes to nothing in service_tokens → passes through.
    await _seed(store)
    r = TestClient(_app(store)).post("/api/v1/projects", headers={"Authorization": "Bearer a.jwt.token"})
    assert r.status_code == 200


async def test_no_auth_not_blocked_by_middleware(store):
    # No bearer → middleware doesn't act; the route's own guard handles authz.
    await _seed(store)
    r = TestClient(_app(store)).post("/api/v1/projects")
    assert r.status_code == 200


async def test_revoked_service_token_falls_through(store):
    # The block targets only LIVE service tokens; a revoked one falls through
    # (its 401 is the route auth's job, not this middleware's).
    await _seed(store, revoked=True)
    r = TestClient(_app(store)).post("/api/v1/projects", headers=_svc())
    assert r.status_code == 200
