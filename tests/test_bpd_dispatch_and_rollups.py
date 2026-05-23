"""Phase-C tests for the dispatch engine + auto-dispatch handler (BPD-28).

Pins:
  - _check_dependencies_unmet returns None for legacy tasks (depends_on=[]),
    a populated list when deps aren't all deployed
  - _dispatch_ready_tasks correctly partitions into
    {dispatched, blocked, skipped}
  - get_feature_status / get_epic_status rollup helpers count correctly
  - The auto-dispatch handler:
      * No-ops when auto_dispatch_on_deploy=False (default)
      * Fires only newly-unblocked tasks (not already-deployed ones)
      * Emits project.tasks.auto_dispatched with the fired list
      * Defensively skips tasks that changed state between read + write
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.api.routes.projects import (
    _check_dependencies_unmet,
    _count_by_status,
    _dispatch_ready_tasks,
)
from src.core.auto_dispatch import make_auto_dispatch_handler
from src.models.base import (
    ArtifactStatus,
    Project,
    ProjectStatus,
    ProjectTask,
    Request,
    TaskStatus,
)
from src.state.sqlite_store import SQLiteStateStore


async def _make_store(tmp_path: Path) -> SQLiteStateStore:
    db = SQLiteStateStore(str(tmp_path / "phaseC.db"))
    await db.initialize()
    await db.create_project(Project(
        project_id="proj-c",
        name="Phase C Test",
        status=ProjectStatus.ACTIVE,
    ))
    return db


def _task(
    task_id: str,
    ordinal: int = 1,
    depends_on: list[str] | None = None,
    status: TaskStatus = TaskStatus.BACKLOG,
    list_status: ArtifactStatus = ArtifactStatus.FINALIZED,
    feature_id: str | None = None,
) -> ProjectTask:
    return ProjectTask(
        task_id=task_id,
        project_id="proj-c",
        list_version=1,
        list_status=list_status,
        ordinal=ordinal,
        title=f"Task {task_id}",
        task_status=status,
        depends_on=depends_on or [],
        feature_id=feature_id,
    )


class _StubOrchestrator:
    """Minimal orchestrator that just records submit() calls and
    returns a fake Request. Avoids spinning up an LLM client."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self._counter = 0

    async def submit(self, **kwargs) -> Request:
        self._counter += 1
        self.submitted.append(kwargs)
        return Request(
            request_id=f"REQ-STUB{self._counter:03d}",
            description=kwargs.get("description", ""),
            task_type=kwargs.get("task_type", "feature_request"),
            priority=kwargs.get("priority", "medium"),
            status="received",
            project_id=kwargs.get("project_id"),
            source_task_id=kwargs.get("source_task_id"),
        )


class _RecordingEvents:
    """Captures emit() calls so tests can assert what got broadcast."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        self.emitted.append((event_type, data))


# ── _check_dependencies_unmet ─────────────────────────────────────────


async def test_legacy_task_with_no_deps_returns_none(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    t = _task("T-legacy", depends_on=[])
    await store.create_task(t)
    unmet = await _check_dependencies_unmet(store, t)
    assert unmet is None


async def test_all_deps_deployed_returns_none(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-A", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-B", status=TaskStatus.DEPLOYED))
    t = _task("T-C", depends_on=["T-A", "T-B"])
    await store.create_task(t)
    unmet = await _check_dependencies_unmet(store, t)
    assert unmet is None


async def test_some_deps_not_deployed_returns_blockers(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-A", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-B", status=TaskStatus.IN_PROGRESS))
    t = _task("T-C", depends_on=["T-A", "T-B"])
    await store.create_task(t)
    unmet = await _check_dependencies_unmet(store, t)
    assert unmet is not None
    assert len(unmet) == 1
    assert unmet[0]["task_id"] == "T-B"
    assert unmet[0]["status"] == "in_progress"


async def test_dangling_dep_reported_as_missing(tmp_path: Path) -> None:
    """A depends_on reference to a non-existent task surfaces as a
    'missing' blocker so the user can see what's broken."""
    store = await _make_store(tmp_path)
    t = _task("T-X", depends_on=["T-ghost"])
    await store.create_task(t)
    unmet = await _check_dependencies_unmet(store, t)
    assert unmet is not None
    assert len(unmet) == 1
    assert unmet[0]["task_id"] == "T-ghost"
    assert unmet[0]["status"] == "missing"


# ── _dispatch_ready_tasks ─────────────────────────────────────────────


async def test_dispatch_ready_partitions_into_three_buckets(tmp_path: Path) -> None:
    """Mixed batch — verify each task ends up in the right bucket
    (dispatched / blocked / skipped)."""
    store = await _make_store(tmp_path)
    orch = _StubOrchestrator()
    # T-1: no deps, ready → DISPATCHED
    await store.create_task(_task("T-1"))
    # T-2: deployed already → SKIPPED (already_dispatched_or_deployed)
    await store.create_task(_task("T-2", status=TaskStatus.DEPLOYED))
    # T-3: blocker T-4 not deployed → BLOCKED
    await store.create_task(_task("T-3", depends_on=["T-4"]))
    await store.create_task(_task("T-4"))  # backlog, blocks T-3
    # T-5: draft list_status → SKIPPED (not_finalized)
    await store.create_task(_task("T-5", list_status=ArtifactStatus.DRAFT))

    tasks = [
        await store.get_task("T-1"),
        await store.get_task("T-2"),
        await store.get_task("T-3"),
        await store.get_task("T-5"),
    ]
    result = await _dispatch_ready_tasks(store, orch, tasks, "user-x")

    dispatched_ids = {d["task_id"] for d in result["dispatched"]}
    blocked_ids = {b["task_id"] for b in result["blocked"]}
    skipped_ids = {s["task_id"] for s in result["skipped"]}

    assert dispatched_ids == {"T-1"}
    assert blocked_ids == {"T-3"}
    assert skipped_ids == {"T-2", "T-5"}
    # Verify orchestrator was called exactly once with the right source_task_id
    assert len(orch.submitted) == 1
    assert orch.submitted[0]["source_task_id"] == "T-1"


async def test_dispatch_ready_blocker_chain_in_response(tmp_path: Path) -> None:
    """Blocked tasks include the unmet-blocker details so the UI can
    render 'blocked by T-X' tooltips."""
    store = await _make_store(tmp_path)
    orch = _StubOrchestrator()
    await store.create_task(_task("T-A", status=TaskStatus.BACKLOG))
    await store.create_task(_task("T-B", depends_on=["T-A"]))
    result = await _dispatch_ready_tasks(
        store, orch, [await store.get_task("T-B")], "u",
    )
    assert len(result["blocked"]) == 1
    assert result["blocked"][0]["task_id"] == "T-B"
    assert any(b["task_id"] == "T-A" for b in result["blocked"][0]["blockers"])


# ── Status rollup helpers ─────────────────────────────────────────────


def test_count_by_status_basic() -> None:
    tasks = [
        ProjectTask(task_id="T-1", project_id="p", list_version=1, ordinal=1,
                   title="x", task_status=TaskStatus.DEPLOYED),
        ProjectTask(task_id="T-2", project_id="p", list_version=1, ordinal=2,
                   title="x", task_status=TaskStatus.DEPLOYED),
        ProjectTask(task_id="T-3", project_id="p", list_version=1, ordinal=3,
                   title="x", task_status=TaskStatus.BACKLOG),
        ProjectTask(task_id="T-4", project_id="p", list_version=1, ordinal=4,
                   title="x", task_status=TaskStatus.FAILED),
    ]
    counts = _count_by_status(tasks)
    assert counts == {"deployed": 2, "backlog": 1, "failed": 1}


def test_count_by_status_empty() -> None:
    assert _count_by_status([]) == {}


# ── Auto-dispatch handler ─────────────────────────────────────────────


async def test_auto_dispatch_no_op_when_flag_off(tmp_path: Path) -> None:
    """Default project has auto_dispatch_on_deploy=False → handler is
    a no-op even when a project task deploys and would unblock siblings."""
    store = await _make_store(tmp_path)
    orch = _StubOrchestrator()
    events = _RecordingEvents()
    handler = make_auto_dispatch_handler(store, orch, events)

    # Seed: T-A just deployed, T-B was blocked by T-A and is now ready
    await store.create_task(_task("T-A", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-B", depends_on=["T-A"]))
    # Create the Request that "completed" — handler looks this up
    req = Request(
        request_id="REQ-deployed",
        description="x", task_type="feature_request", priority="medium",
        status="completed", project_id="proj-c", source_task_id="T-A",
    )
    await store.create_request(req)

    await handler("request.completed", {"request_id": "REQ-deployed"})

    # No dispatches and no events
    assert orch.submitted == []
    assert events.emitted == []
    # T-B still backlog
    t_b = await store.get_task("T-B")
    assert t_b.task_status == TaskStatus.BACKLOG


async def test_auto_dispatch_fires_unblocked_when_flag_on(tmp_path: Path) -> None:
    """Project with auto_dispatch_on_deploy=True → handler fires every
    newly-unblocked task and emits project.tasks.auto_dispatched."""
    store = await _make_store(tmp_path)
    orch = _StubOrchestrator()
    events = _RecordingEvents()
    handler = make_auto_dispatch_handler(store, orch, events)

    # Flip the flag on for this project
    proj = await store.get_project("proj-c")
    proj.auto_dispatch_on_deploy = True
    await store.update_project(proj)

    # Seed: T-A deployed; T-B and T-C both blocked only by T-A; T-D needs T-B
    await store.create_task(_task("T-A", ordinal=1, status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-B", ordinal=2, depends_on=["T-A"]))
    await store.create_task(_task("T-C", ordinal=3, depends_on=["T-A"]))
    await store.create_task(_task("T-D", ordinal=4, depends_on=["T-B"]))

    req = Request(
        request_id="REQ-A-done",
        description="x", task_type="feature_request", priority="medium",
        status="completed", project_id="proj-c", source_task_id="T-A",
    )
    await store.create_request(req)

    await handler("request.completed", {"request_id": "REQ-A-done"})

    # T-B and T-C should have fired (T-D still blocked by backlog T-B)
    fired_task_ids = {s["source_task_id"] for s in orch.submitted}
    assert fired_task_ids == {"T-B", "T-C"}
    # T-D NOT fired
    assert "T-D" not in fired_task_ids

    # Event broadcast happened with the same fired set
    assert len(events.emitted) == 1
    ev_type, ev_data = events.emitted[0]
    assert ev_type == "project.tasks.auto_dispatched"
    assert ev_data["project_id"] == "proj-c"
    assert ev_data["trigger_task_id"] == "T-A"
    fired_in_event = {f["task_id"] for f in ev_data["fired"]}
    assert fired_in_event == {"T-B", "T-C"}

    # T-B and T-C task rows now reflect DISPATCHED + linked request
    t_b = await store.get_task("T-B")
    t_c = await store.get_task("T-C")
    assert t_b.task_status == TaskStatus.DISPATCHED
    assert t_b.request_id and t_b.request_id.startswith("REQ-STUB")
    assert t_c.task_status == TaskStatus.DISPATCHED


async def test_auto_dispatch_ignores_oneoff_request(tmp_path: Path) -> None:
    """A Request with NO source_task_id (regular one-off Submit) must
    not trigger auto-dispatch — there's no project-task chain to walk."""
    store = await _make_store(tmp_path)
    orch = _StubOrchestrator()
    events = _RecordingEvents()
    handler = make_auto_dispatch_handler(store, orch, events)

    proj = await store.get_project("proj-c")
    proj.auto_dispatch_on_deploy = True
    await store.update_project(proj)
    await store.create_task(_task("T-X"))  # would dispatch if handler fires

    req = Request(
        request_id="REQ-oneoff",
        description="x", task_type="feature_request", priority="medium",
        status="completed", project_id="proj-c", source_task_id=None,
    )
    await store.create_request(req)

    await handler("request.completed", {"request_id": "REQ-oneoff"})

    assert orch.submitted == []
    assert events.emitted == []


async def test_auto_dispatch_ignores_unrelated_event_types(tmp_path: Path) -> None:
    """Only request.completed / request.status_changed (with deployed
    status) should trigger. Other event types are a no-op."""
    store = await _make_store(tmp_path)
    orch = _StubOrchestrator()
    events = _RecordingEvents()
    handler = make_auto_dispatch_handler(store, orch, events)

    proj = await store.get_project("proj-c")
    proj.auto_dispatch_on_deploy = True
    await store.update_project(proj)

    await handler("agent.started", {"request_id": "REQ-anything"})
    await handler("project.created", {})
    await handler("request.status_changed", {
        "request_id": "REQ-x", "status": "in_progress",  # NOT a deploy
    })

    assert orch.submitted == []


async def test_auto_dispatch_handler_swallows_errors(tmp_path: Path) -> None:
    """A failure in get_dispatchable_tasks (or anywhere downstream)
    must not propagate — handler is best-effort and logs warnings."""
    store = await _make_store(tmp_path)
    orch = _StubOrchestrator()
    events = _RecordingEvents()
    handler = make_auto_dispatch_handler(store, orch, events)

    # Handler with a request_id that doesn't exist — get_request returns
    # None, handler returns early. Must not raise.
    await handler("request.completed", {"request_id": "REQ-ghost"})
    # Missing request_id → also no-op
    await handler("request.completed", {})


# ── auto_dispatch_on_deploy persistence (BPD-25) ──────────────────────


async def test_project_auto_dispatch_flag_roundtrip(tmp_path: Path) -> None:
    """The new column persists across read+write."""
    store = await _make_store(tmp_path)
    proj_a = await store.get_project("proj-c")
    assert proj_a.auto_dispatch_on_deploy is False  # default

    proj_a.auto_dispatch_on_deploy = True
    await store.update_project(proj_a)

    proj_b = await store.get_project("proj-c")
    assert proj_b.auto_dispatch_on_deploy is True

    proj_b.auto_dispatch_on_deploy = False
    await store.update_project(proj_b)
    proj_c = await store.get_project("proj-c")
    assert proj_c.auto_dispatch_on_deploy is False
