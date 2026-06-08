"""HAI-45 (FR-063, NFR-001) — governed-mode resolution."""

import pytest

from src.auth.service import hash_service_token
from src.core.governance import resolve_governed_mode
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "gov.db"))
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.parametrize("env,expected", [
    ("false", False), ("FALSE", False), ("0", False), ("no", False), ("off", False),
    ("true", True), ("1", True), ("on", True), ("yes", True),
])
async def test_explicit_env_wins(store, env, expected):
    # even with a live token present, an explicit env value is authoritative
    await store.create_service_token("t1", "hermes", hash_service_token("r"), "developer")
    assert await resolve_governed_mode(store, env) is expected


async def test_default_legacy_when_no_service_token(store):
    # no Hermes identity → legacy autonomy (NFR-001)
    assert await resolve_governed_mode(store, None) is False
    assert await resolve_governed_mode(store, "") is False


async def test_default_governed_when_live_token_exists(store):
    await store.create_service_token("t1", "hermes", hash_service_token("r"), "developer")
    assert await resolve_governed_mode(store, None) is True


async def test_revoked_token_does_not_enable_governance(store):
    await store.create_service_token("t1", "hermes", hash_service_token("r"), "developer")
    await store.revoke_service_token("t1")
    assert await resolve_governed_mode(store, None) is False


async def test_lookup_error_falls_back_to_legacy():
    class _Boom:
        async def list_service_tokens(self):
            raise RuntimeError("db down")

    # never freeze automation on a storage hiccup → legacy
    assert await resolve_governed_mode(_Boom(), None) is False
