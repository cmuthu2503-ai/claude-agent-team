"""HAI-21 (FR-030) — proposals table schema (the approval gate's storage)."""

import sqlite3

import pytest

from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "prop.db"))
    await s.initialize()
    yield s
    await s.close()


async def test_proposals_table_has_full_lifecycle_columns(store):
    db = await store._get_db()
    async with db.execute("PRAGMA table_info(proposals)") as cur:
        rows = await cur.fetchall()
    cols = {r["name"] for r in rows}
    expected = {
        "proposal_id", "action_type", "target_ref", "payload_json", "status",
        "proposed_by", "created_at", "decided_by", "decided_at", "executed_at",
        "ttl_seconds", "result_ref", "error", "idempotency_key",
    }
    assert expected <= cols, f"missing columns: {expected - cols}"


async def test_defaults_on_minimal_insert(store):
    db = await store._get_db()
    # Only the NOT NULL columns supplied → defaults fill the rest.
    await db.execute(
        "INSERT INTO proposals (proposal_id, action_type, proposed_by) VALUES (?,?,?)",
        ("prop-1", "project.create", "service:hermes-operator"),
    )
    await db.commit()
    async with db.execute(
        "SELECT status, ttl_seconds, payload_json FROM proposals WHERE proposal_id='prop-1'"
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "pending"
    assert row["ttl_seconds"] == 86400
    assert row["payload_json"] == "{}"


async def test_idempotency_key_is_unique(store):
    db = await store._get_db()
    await db.execute(
        "INSERT INTO proposals (proposal_id, action_type, proposed_by, idempotency_key) VALUES (?,?,?,?)",
        ("prop-a", "deploy", "service:x", "idem-1"),
    )
    await db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO proposals (proposal_id, action_type, proposed_by, idempotency_key) VALUES (?,?,?,?)",
            ("prop-b", "deploy", "service:x", "idem-1"),  # duplicate key
        )
        await db.commit()


async def test_multiple_null_idempotency_keys_allowed(store):
    # SQLite UNIQUE permits multiple NULLs — proposals without an idempotency key
    # must not collide.
    db = await store._get_db()
    await db.execute(
        "INSERT INTO proposals (proposal_id, action_type, proposed_by) VALUES ('p1','deploy','x')"
    )
    await db.execute(
        "INSERT INTO proposals (proposal_id, action_type, proposed_by) VALUES ('p2','deploy','x')"
    )
    await db.commit()
    async with db.execute("SELECT COUNT(*) AS n FROM proposals") as cur:
        assert (await cur.fetchone())["n"] == 2
