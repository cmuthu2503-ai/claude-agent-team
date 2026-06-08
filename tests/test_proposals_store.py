"""HAI-22 (FR-030) — Proposal model + store CRUD/transition methods."""

import pytest

from src.models.base import Proposal, ProposalStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "prop.db"))
    await s.initialize()
    yield s
    await s.close()


def _p(pid: str = "prop-1", **kw) -> Proposal:
    base = dict(proposal_id=pid, action_type="project.create", proposed_by="service:hermes-operator")
    base.update(kw)
    return Proposal(**base)


async def test_create_get_roundtrip(store):
    await store.create_proposal(_p(payload={"name": "Atlas"}, target_ref="proj-1", idempotency_key="k1"))
    got = await store.get_proposal("prop-1")
    assert got is not None
    assert got.action_type == "project.create"
    assert got.payload == {"name": "Atlas"}          # JSON round-trips
    assert got.target_ref == "proj-1"
    assert got.status == ProposalStatus.PENDING
    assert got.ttl_seconds == 86400
    assert got.proposed_by == "service:hermes-operator"
    assert got.is_terminal is False


async def test_get_unknown_returns_none(store):
    assert await store.get_proposal("nope") is None


async def test_get_by_idempotency_key(store):
    await store.create_proposal(_p(idempotency_key="idem-x"))
    got = await store.get_proposal_by_idempotency_key("idem-x")
    assert got is not None and got.proposal_id == "prop-1"
    assert await store.get_proposal_by_idempotency_key("missing") is None


async def test_list_filter_and_order(store):
    await store.create_proposal(_p("prop-a", action_type="deploy"))
    await store.create_proposal(_p("prop-b", action_type="rollback"))
    await store.update_proposal("prop-a", {"status": ProposalStatus.CONFIRMED})

    all_ids = [p.proposal_id for p in await store.list_proposals()]
    assert set(all_ids) == {"prop-a", "prop-b"}
    assert all_ids[0] == "prop-b"  # newest first
    assert [p.proposal_id for p in await store.list_proposals(status="pending")] == ["prop-b"]
    assert [p.proposal_id for p in await store.list_proposals(status="confirmed")] == ["prop-a"]


async def test_update_transition_fields(store):
    await store.create_proposal(_p())
    updated = await store.update_proposal(
        "prop-1", {"status": ProposalStatus.CONFIRMED, "decided_by": "alice"}
    )
    assert updated.status == ProposalStatus.CONFIRMED
    assert updated.decided_by == "alice"
    assert updated.is_terminal is False  # confirmed is not terminal


async def test_update_no_allowed_fields_returns_existing(store):
    await store.create_proposal(_p())
    same = await store.update_proposal("prop-1", {"bogus": "x"})  # ignored
    assert same.status == ProposalStatus.PENDING


async def test_list_filters_by_action_type_and_proposer(store):
    await store.create_proposal(_p("p1", action_type="deploy", proposed_by="service:hermes"))
    await store.create_proposal(_p("p2", action_type="deploy", proposed_by="alice"))
    await store.create_proposal(_p("p3", action_type="rollback", proposed_by="alice"))

    assert {p.proposal_id for p in await store.list_proposals(action_type="deploy")} == {"p1", "p2"}
    assert {p.proposal_id for p in await store.list_proposals(proposed_by="alice")} == {"p2", "p3"}
    # combined filters AND together
    both = await store.list_proposals(action_type="deploy", proposed_by="alice")
    assert [p.proposal_id for p in both] == ["p2"]


async def test_transition_proposal_is_atomic_cas(store):
    await store.create_proposal(_p())
    # pending → confirmed wins once
    assert await store.transition_proposal("prop-1", "pending", "confirmed", decided_by="bob") is True
    # a second pending→confirmed loses (no longer pending)
    assert await store.transition_proposal("prop-1", "pending", "confirmed") is False
    got = await store.get_proposal("prop-1")
    assert got.status == ProposalStatus.CONFIRMED
    assert got.decided_by == "bob"
