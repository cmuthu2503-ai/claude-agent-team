"""HAI-57 (FR-035c) — crash-recovery reconciliation of stranded proposals.

A proposal confirmed but not yet executed when the process died is stranded in
`confirmed` (executed_at NULL). Startup reconciliation marks it `failed` so it
never hangs — and never re-drives a possibly-half-applied side effect.
"""

from datetime import datetime

import pytest

from src.core.events import EventEmitter
from src.core.proposal_recovery import RECONCILE_ERROR, reconcile_confirmed_proposals_once
from src.models.base import Proposal, ProposalStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "rec.db"))
    await s.initialize()
    yield s
    await s.close()


def _events_with_capture():
    em = EventEmitter()
    captured: list = []

    async def cap(et, d):
        captured.append((et, d))

    em.on(cap)
    return em, captured


async def _stranded(store, pid: str) -> Proposal:
    """A proposal confirmed but never executed — the crash window."""
    p = Proposal(proposal_id=pid, action_type="deploy", proposed_by="service:hermes")
    await store.create_proposal(p)
    await store.transition_proposal(pid, "pending", "confirmed", decided_by="alice")
    return p


async def test_reconciles_confirmed_to_failed_and_emits(store):
    em, captured = _events_with_capture()
    await _stranded(store, "p1")
    now = datetime.utcnow()

    reconciled = await reconcile_confirmed_proposals_once(store, em, now=now)
    assert reconciled == ["p1"]
    got = await store.get_proposal("p1")
    assert got.status == ProposalStatus.FAILED
    assert got.error == RECONCILE_ERROR
    assert got.executed_at is not None                 # stamped so it's not "still running"
    assert any(
        et == "proposal.failed" and d["proposal_id"] == "p1" and d.get("reconciled") is True
        for et, d in captured
    )


async def test_pending_is_not_reconciled(store):
    em, _ = _events_with_capture()
    p = Proposal(proposal_id="p1", action_type="deploy", proposed_by="service:hermes")
    await store.create_proposal(p)                     # stays pending
    assert await reconcile_confirmed_proposals_once(store, em) == []
    assert (await store.get_proposal("p1")).status == ProposalStatus.PENDING


async def test_executed_is_not_reconciled(store):
    em, _ = _events_with_capture()
    await _stranded(store, "p1")
    # the executor DID finish before the (hypothetical) crash
    await store.update_proposal("p1", {"status": ProposalStatus.EXECUTED, "result_ref": "OK"})
    assert await reconcile_confirmed_proposals_once(store, em) == []
    got = await store.get_proposal("p1")
    assert got.status == ProposalStatus.EXECUTED
    assert got.result_ref == "OK"


async def test_recovery_is_idempotent(store):
    em, _ = _events_with_capture()
    await _stranded(store, "p1")
    assert await reconcile_confirmed_proposals_once(store, em) == ["p1"]
    # a second pass finds nothing left in `confirmed`
    assert await reconcile_confirmed_proposals_once(store, em) == []
    assert (await store.get_proposal("p1")).status == ProposalStatus.FAILED


async def test_reconciles_multiple_and_leaves_others(store):
    em, _ = _events_with_capture()
    await _stranded(store, "p1")
    await _stranded(store, "p2")
    # a pending and a rejected one should be untouched
    await store.create_proposal(
        Proposal(proposal_id="p3", action_type="deploy", proposed_by="service:hermes")
    )
    await store.transition_proposal("p3", "pending", "rejected")  # p3 → rejected, untouched

    reconciled = set(await reconcile_confirmed_proposals_once(store, em))
    assert reconciled == {"p1", "p2"}
    assert (await store.get_proposal("p3")).status == ProposalStatus.REJECTED


async def test_emit_failure_does_not_break_recovery(store):
    """A bad event sink must not strand the proposal — the DB transition still
    lands and the id is still returned."""

    class BoomEvents:
        async def emit(self, *a, **k):
            raise RuntimeError("sink down")

    await _stranded(store, "p1")
    reconciled = await reconcile_confirmed_proposals_once(store, BoomEvents())
    assert reconciled == ["p1"]
    assert (await store.get_proposal("p1")).status == ProposalStatus.FAILED


async def test_works_without_events(store):
    await _stranded(store, "p1")
    assert await reconcile_confirmed_proposals_once(store, None) == ["p1"]
    assert (await store.get_proposal("p1")).status == ProposalStatus.FAILED
