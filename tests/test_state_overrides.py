"""PAM-16 — consolidated state-layer contract for agent_model_overrides.

The granular per-method behaviour is pinned in
``test_agent_model_overrides.py``. This file is the named spec target
from PAM-16 and asserts the higher-level invariants the API + UI
depend on:

  - Multiple overrides can coexist (PK is per-agent, not per-row)
  - Bulk clear is atomic — no partial-clear state visible mid-operation
  - The list ordering contract (most-recent-first) holds across many rows
  - updated_by is preserved verbatim (audit-trail invariant)
  - Re-set on an existing row updates updated_at AND updated_by
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "ov.db"))
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


@pytest.mark.asyncio
async def test_multiple_overrides_coexist(store):
    """One row per agent. Five agents → five rows, none clobber."""
    pairs = [
        ("backend_specialist", "claude-haiku-4-7"),
        ("tester_specialist",  "claude-sonnet-4-7"),
        ("frontend_specialist", "claude-opus-4-7"),
        ("prd_specialist",     "claude-haiku-4-7"),
        ("devops_specialist",  "claude-sonnet-4-7"),
    ]
    for agent_id, model_id in pairs:
        await store.set_agent_model_override(agent_id, model_id, "alice")
    rows = await store.list_agent_model_overrides()
    assert len(rows) == 5
    by_agent = {r["agent_id"]: r["model_id"] for r in rows}
    for agent_id, model_id in pairs:
        assert by_agent[agent_id] == model_id


@pytest.mark.asyncio
async def test_bulk_clear_is_atomic(store):
    """Bulk clear → list returns [] (no partial state)."""
    for i in range(10):
        await store.set_agent_model_override(f"agent_{i}", "claude-opus-4-7")
    assert len(await store.list_agent_model_overrides()) == 10
    n = await store.clear_all_agent_model_overrides()
    assert n == 10
    assert await store.list_agent_model_overrides() == []


@pytest.mark.asyncio
async def test_updated_by_is_preserved_verbatim(store):
    """The audit trail depends on the updated_by string round-tripping
    intact — no normalisation, no truncation."""
    await store.set_agent_model_override("a", "claude-opus-4-7", "alice@example.com")
    rows = await store.list_agent_model_overrides()
    assert rows[0]["updated_by"] == "alice@example.com"


@pytest.mark.asyncio
async def test_reset_updates_both_model_and_updater(store):
    """Operator reassigns the override → row reflects new model AND new
    updated_by (the audit-trail invariant). updated_at MUST move forward."""
    await store.set_agent_model_override("a", "claude-opus-4-7", "alice")
    first = (await store.list_agent_model_overrides())[0]

    # Sleep > 1s so SQLite's second-resolution CURRENT_TIMESTAMP moves.
    await asyncio.sleep(1.1)

    await store.set_agent_model_override("a", "claude-haiku-4-7", "bob")
    rows = await store.list_agent_model_overrides()
    assert len(rows) == 1  # still one row (PK upsert)
    second = rows[0]
    assert second["model_id"] == "claude-haiku-4-7"
    assert second["updated_by"] == "bob"
    assert second["updated_at"] > first["updated_at"]


@pytest.mark.asyncio
async def test_list_ordering_holds_at_scale(store):
    """20 overrides set in known order → list returns them most-recent-first.
    rowid tiebreaker handles the sub-second clusters."""
    ids = [f"agent_{i:02d}" for i in range(20)]
    for agent_id in ids:
        await store.set_agent_model_override(agent_id, "claude-opus-4-7")
    rows = await store.list_agent_model_overrides()
    listed = [r["agent_id"] for r in rows]
    assert listed == list(reversed(ids))


@pytest.mark.asyncio
async def test_delete_one_doesnt_touch_others(store):
    await store.set_agent_model_override("a", "claude-opus-4-7")
    await store.set_agent_model_override("b", "claude-haiku-4-7")
    await store.set_agent_model_override("c", "claude-sonnet-4-7")
    assert await store.delete_agent_model_override("b") is True
    remaining = await store.list_agent_model_overrides()
    by_agent = {r["agent_id"]: r["model_id"] for r in remaining}
    assert by_agent == {
        "a": "claude-opus-4-7",
        "c": "claude-sonnet-4-7",
    }
