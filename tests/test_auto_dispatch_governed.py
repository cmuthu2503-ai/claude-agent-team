"""HAI-46 (FR-060) — auto-dispatch routes through the gate in governed mode."""

import pytest

from src.core.auto_dispatch import make_auto_dispatch_handler
from src.core.events import EventEmitter
from src.models.base import (
    ArtifactStatus,
    Project,
    ProjectStatus,
    ProjectTask,
    ProposalStatus,
    Request,
    TaskStatus,
)
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "ad.db"))
    await s.initialize()
    yield s
    await s.close()


class _Orch:
    def __init__(self):
        self.submitted = []

    async def submit(self, **kw):
        self.submitted.append(kw)
        return Request(request_id=f"REQ-{len(self.submitted)}", description=kw.get("description", ""))


def _events():
    em = EventEmitter()
    cap: list = []

    async def h(et, d):
        cap.append((et, d))

    em.on(h)
    return em, cap


async def _seed_unblocked(store, monkeypatch, project_id="proj-1"):
    """A project with auto_dispatch on + one finalized/backlog task + a completed
    trigger request. The dispatchable-set computation (get_dispatchable_tasks) is
    monkeypatched to return our task, so these tests isolate HAI-46's gate routing
    from BPD's already-tested dispatchable filter."""
    await store.create_project(
        Project(project_id=project_id, name="Atlas", status=ProjectStatus.ACTIVE)
    )
    # create_project doesn't persist the flag — toggle it like the BPD tests do.
    proj = await store.get_project(project_id)
    proj.auto_dispatch_on_deploy = True
    await store.update_project(proj)
    task = ProjectTask(
        task_id="T-1", project_id=project_id, feature_id="F-1",
        list_version=1, ordinal=1,
        title="Build X", description="build the thing",
        task_status=TaskStatus.BACKLOG, list_status=ArtifactStatus.FINALIZED,
    )
    await store.create_task(task)
    trigger = Request(
        request_id="REQ-TRIG", description="trigger", project_id=project_id, source_task_id="T-0",
    )
    await store.create_request(trigger)

    async def _ready(pid):
        return [task]

    monkeypatch.setattr(store, "get_dispatchable_tasks", _ready)
    return task


async def test_governed_mode_creates_proposal_not_dispatch(store, monkeypatch):
    task = await _seed_unblocked(store, monkeypatch)
    orch = _Orch()
    em, cap = _events()
    handler = make_auto_dispatch_handler(store, orch, em, governed=lambda: True)

    await handler("request.completed", {"request_id": "REQ-TRIG"})

    # NO direct submit happened; a task.dispatch proposal was created instead.
    assert orch.submitted == []
    proposals = await store.list_proposals(action_type="task.dispatch")
    assert len(proposals) == 1
    assert proposals[0].payload["task_ids"] == ["T-1"]
    assert proposals[0].status == ProposalStatus.PENDING
    assert proposals[0].proposed_by == "system:auto-dispatch"
    # the task is still BACKLOG (nothing dispatched yet — awaits human confirm)
    assert (await store.get_task("T-1")).task_status == TaskStatus.BACKLOG
    # notifications: proposal.created + dispatch_proposed, NOT auto_dispatched
    types = [et for et, _ in cap]
    assert "proposal.created" in types
    assert "project.tasks.dispatch_proposed" in types
    assert "project.tasks.auto_dispatched" not in types


async def test_legacy_mode_dispatches_directly(store, monkeypatch):
    await _seed_unblocked(store, monkeypatch)
    orch = _Orch()
    em, cap = _events()
    # default (no governed predicate) → legacy, unchanged behavior
    handler = make_auto_dispatch_handler(store, orch, em)

    await handler("request.completed", {"request_id": "REQ-TRIG"})

    assert len(orch.submitted) == 1
    assert orch.submitted[0]["source_task_id"] == "T-1"
    assert (await store.get_task("T-1")).task_status == TaskStatus.DISPATCHED
    assert await store.list_proposals(action_type="task.dispatch") == []
    assert "project.tasks.auto_dispatched" in [et for et, _ in cap]


async def test_governed_dispatch_is_idempotent_per_trigger(store, monkeypatch):
    await _seed_unblocked(store, monkeypatch)
    orch = _Orch()
    em, _ = _events()
    handler = make_auto_dispatch_handler(store, orch, em, governed=lambda: True)

    await handler("request.completed", {"request_id": "REQ-TRIG"})
    await handler("request.completed", {"request_id": "REQ-TRIG"})  # re-emit same trigger
    # only ONE proposal — idempotency_key dedups
    assert len(await store.list_proposals(action_type="task.dispatch")) == 1
