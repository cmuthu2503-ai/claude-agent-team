"""HAI-29 (FR-036) — proposal auto-expire sweeper."""

import asyncio
from datetime import datetime, timedelta

import pytest

from src.core.events import EventEmitter
from src.core.proposal_expiry import make_proposal_expiry_sweeper, sweep_expired_once
from src.models.base import Proposal, ProposalStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "exp.db"))
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


async def _make_pending(store, pid: str, ttl: int = 86400) -> Proposal:
    p = Proposal(proposal_id=pid, action_type="deploy", proposed_by="service:hermes", ttl_seconds=ttl)
    await store.create_proposal(p)
    return p


async def test_expires_stale_pending_and_emits(store):
    em, captured = _events_with_capture()
    await _make_pending(store, "p1", ttl=3600)            # created now, ttl 1h
    now = datetime.utcnow() + timedelta(hours=2)          # 2h later → stale

    expired = await sweep_expired_once(store, em, now=now)
    assert expired == ["p1"]
    assert (await store.get_proposal("p1")).status == ProposalStatus.EXPIRED
    assert any(et == "proposal.expired" and d["proposal_id"] == "p1" for et, d in captured)


async def test_not_yet_due_stays_pending(store):
    em, _ = _events_with_capture()
    await _make_pending(store, "p1", ttl=86400)
    now = datetime.utcnow() + timedelta(hours=1)          # only 1h later
    assert await sweep_expired_once(store, em, now=now) == []
    assert (await store.get_proposal("p1")).status == ProposalStatus.PENDING


async def test_confirmed_proposal_is_never_expired(store):
    em, _ = _events_with_capture()
    await _make_pending(store, "p1", ttl=1)
    await store.transition_proposal("p1", "pending", "confirmed")  # human acted first
    now = datetime.utcnow() + timedelta(hours=2)
    assert await sweep_expired_once(store, em, now=now) == []      # not pending → skipped
    assert (await store.get_proposal("p1")).status == ProposalStatus.CONFIRMED


async def test_sweeper_loop_expires(store):
    em, captured = _events_with_capture()
    await _make_pending(store, "p1", ttl=0)               # due immediately
    task = asyncio.create_task(make_proposal_expiry_sweeper(store, em, interval=0.05)())
    try:
        for _ in range(40):
            await asyncio.sleep(0.05)
            if (await store.get_proposal("p1")).status == ProposalStatus.EXPIRED:
                break
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await store.get_proposal("p1")).status == ProposalStatus.EXPIRED
    assert any(et == "proposal.expired" for et, _ in captured)
