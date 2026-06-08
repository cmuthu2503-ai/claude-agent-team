"""HAI-01 (FR-010) — service_tokens schema + store methods.

Long-lived headless identities (e.g. the Hermes Agent integration). Only the
SHA-256 hash is persisted; the raw token is shown once and never stored.
"""

import hashlib

import pytest

from src.models.base import ServiceToken, UserRole
from src.state.sqlite_store import SQLiteStateStore


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "svc.db"))
    await s.initialize()
    yield s
    await s.close()


async def test_create_and_lookup_by_hash(store):
    raw = "hermes-secret-abc123"
    await store.create_service_token(
        token_id="stok-1", name="hermes-operator", hashed_token=_hash(raw), role="developer",
    )
    tok = await store.get_service_token_by_hash(_hash(raw))
    assert tok is not None
    assert isinstance(tok, ServiceToken)
    assert tok.token_id == "stok-1"
    assert tok.name == "hermes-operator"
    assert tok.role == UserRole.DEVELOPER
    assert tok.is_revoked is False
    assert tok.last_used_at is None


async def test_unknown_hash_returns_none(store):
    assert await store.get_service_token_by_hash(_hash("nope")) is None


async def test_hash_not_exposed_on_model(store):
    await store.create_service_token("stok-2", "monitor", _hash("x"), "viewer")
    tok = await store.get_service_token_by_hash(_hash("x"))
    # The hash must never round-trip onto the model handed to callers.
    assert not hasattr(tok, "hashed_token")


async def test_list_newest_first(store):
    await store.create_service_token("stok-a", "a", _hash("a"), "viewer")
    await store.create_service_token("stok-b", "b", _hash("b"), "admin")
    tokens = await store.list_service_tokens()
    ids = [t.token_id for t in tokens]
    assert set(ids) == {"stok-a", "stok-b"}
    assert ids[0] == "stok-b"  # newest first


async def test_revoke_is_idempotent_and_blocks(store):
    await store.create_service_token("stok-r", "r", _hash("r"), "admin")
    assert await store.revoke_service_token("stok-r") is True   # live → revoked
    assert await store.revoke_service_token("stok-r") is False  # already revoked
    assert await store.revoke_service_token("stok-missing") is False  # never existed

    tok = await store.get_service_token_by_hash(_hash("r"))
    assert tok.is_revoked is True
    assert tok.revoked_at is not None


async def test_touch_last_used(store):
    await store.create_service_token("stok-t", "t", _hash("t"), "viewer")
    assert (await store.get_service_token_by_hash(_hash("t"))).last_used_at is None
    await store.touch_service_token_last_used("stok-t")
    assert (await store.get_service_token_by_hash(_hash("t"))).last_used_at is not None
