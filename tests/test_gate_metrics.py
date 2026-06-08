"""HAI-62 (FR-083) — gate observability signals."""

from datetime import datetime, timedelta

import pytest

from src.auth.service import hash_service_token
from src.core.gate_metrics import build_gate_metrics
from src.models.base import Proposal, ProposalStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "metrics.db"))
    await s.initialize()
    yield s
    await s.close()


async def _mk(store, pid, status):
    p = Proposal(proposal_id=pid, action_type="deploy", proposed_by="service:hermes")
    await store.create_proposal(p)
    if status != "pending":
        await store.update_proposal(pid, {"status": status})


async def test_status_counts_store_method(store):
    await _mk(store, "p1", "pending")
    await _mk(store, "p2", "pending")
    await _mk(store, "p3", "executed")
    counts = await store.proposal_status_counts()
    assert counts["pending"] == 2 and counts["executed"] == 1


async def test_backlog_depth_and_expired_rate(store):
    # 3 pending (backlog) + decided set: 2 executed, 1 failed, 1 rejected, 2 expired
    for i in range(3):
        await _mk(store, f"pend{i}", "pending")
    await _mk(store, "e1", "executed")
    await _mk(store, "e2", "executed")
    await _mk(store, "f1", "failed")
    await _mk(store, "r1", "rejected")
    await _mk(store, "x1", "expired")
    await _mk(store, "x2", "expired")

    m = await build_gate_metrics(store)
    assert m["pending_backlog_depth"] == 3
    # decided = 2+1+1+2 = 6; expired 2 → 2/6 = 0.3333
    assert m["expired_without_action_rate"] == round(2 / 6, 4)
    assert m["proposals_by_status"]["pending"] == 3
    assert m["proposals_total"] == 9


async def test_expired_rate_zero_when_nothing_decided(store):
    await _mk(store, "p1", "pending")
    m = await build_gate_metrics(store)
    assert m["expired_without_action_rate"] == 0.0      # no division by zero


async def test_service_token_activity(store):
    now = datetime.utcnow()
    await store.create_service_token("t1", "hermes", hash_service_token("raw1"), "developer")
    await store.create_service_token("t2", "old", hash_service_token("raw2"), "viewer")
    await store.create_service_token("t3", "dead", hash_service_token("raw3"), "viewer")
    await store.revoke_service_token("t3")
    # t1 used "now" (recent); t2 used 3 days ago (outside the 24h window) — set
    # directly since touch_* always stamps the current time.
    await store.touch_service_token_last_used("t1")
    db = await store._get_db()
    await db.execute(
        "UPDATE service_tokens SET last_used_at = ? WHERE token_id = ?",
        ((now - timedelta(days=3)).isoformat(), "t2"),
    )
    await db.commit()

    m = await build_gate_metrics(store, now=now + timedelta(seconds=5), recent_window_seconds=86400)
    st = m["service_tokens"]
    assert st["active"] == 2 and st["revoked"] == 1
    assert st["recently_used"] == 1                     # only t1 within the window


async def test_metrics_never_raises_on_token_lookup_error(store):
    class _S:
        async def proposal_status_counts(self):
            return {"pending": 1}

        async def list_service_tokens(self):
            raise RuntimeError("db down")

    m = await build_gate_metrics(_S())
    assert m["pending_backlog_depth"] == 1
    assert m["service_tokens"]["active"] == 0           # degraded, not crashed
