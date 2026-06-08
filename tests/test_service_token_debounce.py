"""HAI-52 (FR-015) — last_used_at write debounce.

Coalesces the per-request last_used_at write to ≤ once/window/token so a chatty
service client doesn't contend with the supervisor on the shared SQLite DB.
"""

import types

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from src.auth import service as svc
from src.auth.service import _should_touch_last_used, get_service_principal, hash_service_token
from src.state.sqlite_store import SQLiteStateStore


def test_first_touch_allowed_then_debounced():
    svc._last_used_touched.pop("stok-deb-1", None)
    tid = "stok-deb-1"
    assert _should_touch_last_used(tid, now=1000.0) is True       # first → write
    assert _should_touch_last_used(tid, now=1030.0) is False      # within window → skip
    assert _should_touch_last_used(tid, now=1059.9) is False
    assert _should_touch_last_used(tid, now=1060.1) is True       # past window → write again
    assert _should_touch_last_used(tid, now=1070.0) is False      # debounced from new mark


def test_per_token_windows_are_independent():
    svc._last_used_touched.pop("stok-A", None)
    svc._last_used_touched.pop("stok-B", None)
    assert _should_touch_last_used("stok-A", now=5000.0) is True
    assert _should_touch_last_used("stok-B", now=5000.0) is True   # own window
    assert _should_touch_last_used("stok-A", now=5001.0) is False


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "deb.db"))
    await s.initialize()
    yield s
    await s.close()


def _req(store):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(state_store=store))
    )


async def test_get_service_principal_writes_last_used_only_once(store):
    svc._last_used_touched.pop("stok-deb-int", None)
    raw = "debounce-integration-token"
    await store.create_service_token("stok-deb-int", "h", hash_service_token(raw), "viewer")

    calls: list[str] = []
    orig = store.touch_service_token_last_used

    async def spy(token_id: str):
        calls.append(token_id)
        await orig(token_id)

    store.touch_service_token_last_used = spy  # type: ignore[method-assign]

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=raw)
    await get_service_principal(credentials=creds, request=_req(store))
    await get_service_principal(credentials=creds, request=_req(store))

    # Two rapid authenticated calls → exactly one DB write (the second debounced).
    assert calls == ["stok-deb-int"]
