"""HAI-50 (M1, M5) — gate audit query: zero ungated executions by the service principal."""

import pytest

from src.core.gate_audit import audit_service_principal_gating
from src.models.base import Proposal
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "audit.db"))
    await s.initialize()
    yield s
    await s.close()


async def _mk(store, pid, *, proposed_by, status="pending", decided_by=None):
    await store.create_proposal(
        Proposal(proposal_id=pid, action_type="deploy", proposed_by=proposed_by)
    )
    fields = {"status": status}
    if decided_by is not None:
        fields["decided_by"] = decided_by
    if status != "pending":
        await store.update_proposal(pid, fields)


async def test_clean_when_humans_decide(store):
    # service proposes; a human confirms+executes → clean (the normal flow)
    await _mk(store, "p1", proposed_by="service:hermes", status="executed", decided_by="alice")
    await _mk(store, "p2", proposed_by="service:hermes", status="pending")
    await _mk(store, "p3", proposed_by="service:hermes", status="rejected", decided_by="bob")

    audit = await audit_service_principal_gating(store)
    assert audit["clean"] is True
    assert audit["ungated_executions"] == 0
    assert audit["service_proposed_total"] == 3
    assert audit["service_proposed_executed"] == 1
    assert audit["executed_decided_by"] == ["alice"]


async def test_channel_token_decision_is_not_a_breach(store):
    # the one-time channel token is HUMAN authority, not a service identity
    await _mk(store, "p1", proposed_by="service:hermes", status="executed",
              decided_by="channel:one-time-token")
    audit = await audit_service_principal_gating(store)
    assert audit["clean"] is True
    assert audit["ungated_executions"] == 0


async def test_detects_self_approval_breach(store):
    # inject the one shape that would be a breach: a SERVICE identity as the decider.
    # (the live routes make this impossible — confirm is human-only — but the audit
    # must actually DETECT it, not just always return clean.)
    await _mk(store, "p1", proposed_by="service:hermes", status="executed",
              decided_by="service:hermes")
    audit = await audit_service_principal_gating(store)
    assert audit["clean"] is False
    assert audit["ungated_executions"] == 1
    assert audit["ungated_execution_proposal_ids"] == ["p1"]


async def test_empty_store_is_clean(store):
    audit = await audit_service_principal_gating(store)
    assert audit["clean"] is True and audit["service_proposed_total"] == 0
