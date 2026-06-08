"""HAI-02 (FR-010..012) — service-token auth dependency.

Authenticates a long-lived service token (Authorization: Bearer <token>) into a
machine principal shaped like a user payload, tagged is_service_token=True.
Tests call the dependency directly with a stub Request.
"""

import types

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.auth.service import get_service_principal, hash_service_token, principal_actor
from src.state.sqlite_store import SQLiteStateStore


def _creds(raw: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=raw)


def _request_with(store: SQLiteStateStore):
    # Mimic the request.app.state.state_store path the dependency reads.
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(state_store=store))
    )


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "auth.db"))
    await s.initialize()
    yield s
    await s.close()


async def test_valid_token_returns_machine_principal(store):
    raw = "secret-xyz"
    await store.create_service_token("stok-9", "hermes-operator", hash_service_token(raw), "developer")

    principal = await get_service_principal(credentials=_creds(raw), request=_request_with(store))

    assert principal["role"] == "developer"
    assert principal["is_service_token"] is True
    assert principal["sub"] == "stok-9"
    assert principal["token_id"] == "stok-9"
    assert principal["username"] == "hermes-operator"


async def test_valid_token_stamps_last_used(store):
    raw = "touch-me"
    await store.create_service_token("stok-t", "t", hash_service_token(raw), "viewer")
    assert (await store.get_service_token_by_hash(hash_service_token(raw))).last_used_at is None

    await get_service_principal(credentials=_creds(raw), request=_request_with(store))

    assert (await store.get_service_token_by_hash(hash_service_token(raw))).last_used_at is not None


async def test_missing_credentials_401(store):
    with pytest.raises(HTTPException) as exc:
        await get_service_principal(credentials=None, request=_request_with(store))
    assert exc.value.status_code == 401


async def test_unknown_token_401(store):
    with pytest.raises(HTTPException) as exc:
        await get_service_principal(credentials=_creds("not-a-real-token"), request=_request_with(store))
    assert exc.value.status_code == 401


async def test_revoked_token_401(store):
    raw = "revoke-me"
    await store.create_service_token("stok-rv", "old", hash_service_token(raw), "admin")
    await store.revoke_service_token("stok-rv")

    with pytest.raises(HTTPException) as exc:
        await get_service_principal(credentials=_creds(raw), request=_request_with(store))
    assert exc.value.status_code == 401
    assert "revoked" in exc.value.detail.lower()


def test_hash_is_sha256_hex():
    import hashlib

    assert hash_service_token("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(hash_service_token("abc")) == 64


def test_principal_actor_attribution():
    # Service token → service:<name>, falling back to the token id.
    assert principal_actor({"is_service_token": True, "username": "hermes-monitor", "token_id": "stok-1"}) == "service:hermes-monitor"
    assert principal_actor({"is_service_token": True, "username": "", "token_id": "stok-1"}) == "service:stok-1"
    # Human → username, falling back to sub, then 'unknown'.
    assert principal_actor({"username": "alice", "sub": "u1"}) == "alice"
    assert principal_actor({"sub": "u1"}) == "u1"
    assert principal_actor({}) == "unknown"
