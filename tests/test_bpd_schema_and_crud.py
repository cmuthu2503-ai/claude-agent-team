"""Phase-A regression tests for Build Plan Decomposition (BPD-09).

Pins the schema + CRUD + dependency-graph contract from BPD-02..08:

  - Epic + Feature CRUD round-trips
  - ProjectTask BPD-field round-trips (new fields persist + read back)
  - Legacy task compatibility (rows without BPD fields default cleanly)
  - Cascade semantics: epic delete → features delete + task feature_id NULL
  - Dependency graph helpers: get_task_blockers, get_dispatchable_tasks,
    has_task_cycle (cycle vs linear DAG vs self-loop)

Setup uses a fresh SQLiteStateStore per test with a seeded project,
so each test runs in isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.models.base import (
    ArtifactStatus,
    Epic,
    Feature,
    Project,
    ProjectStatus,
    ProjectTask,
    TaskStatus,
)
from src.state.sqlite_store import SQLiteStateStore


async def _make_store(tmp_path: Path) -> SQLiteStateStore:
    db = SQLiteStateStore(str(tmp_path / "bpd.db"))
    await db.initialize()
    await db.create_project(Project(
        project_id="proj-bpd-test",
        name="BPD Test",
        status=ProjectStatus.ACTIVE,
    ))
    return db


def _epic(epic_id: str, ordinal: int = 1, version: int = 1, **kw) -> Epic:
    defaults = dict(
        epic_id=epic_id,
        project_id="proj-bpd-test",
        list_version=version,
        list_status=ArtifactStatus.DRAFT,
        ordinal=ordinal,
        title=f"Epic {epic_id}",
        description="x",
        acceptance_criteria="done when X happens",
    )
    defaults.update(kw)
    return Epic(**defaults)


def _feature(
    feature_id: str, epic_id: str, ordinal: int = 1, **kw,
) -> Feature:
    defaults = dict(
        feature_id=feature_id,
        epic_id=epic_id,
        project_id="proj-bpd-test",
        list_version=1,
        list_status=ArtifactStatus.DRAFT,
        ordinal=ordinal,
        title=f"Feature {feature_id}",
        description="x",
        acceptance_criteria="done when Y",
    )
    defaults.update(kw)
    return Feature(**defaults)


def _task(
    task_id: str,
    ordinal: int = 1,
    feature_id: str | None = None,
    depends_on: list[str] | None = None,
    status: TaskStatus = TaskStatus.BACKLOG,
    list_status: ArtifactStatus = ArtifactStatus.FINALIZED,
    **kw,
) -> ProjectTask:
    defaults = dict(
        task_id=task_id,
        project_id="proj-bpd-test",
        list_version=1,
        list_status=list_status,
        ordinal=ordinal,
        title=f"Task {task_id}",
        task_status=status,
        feature_id=feature_id,
        depends_on=depends_on or [],
        primary_file=f"src/{task_id}.py",
        expected_loc=100,
        acceptance_test=f"{task_id} works as expected",
    )
    defaults.update(kw)
    return ProjectTask(**defaults)


# ── Epic CRUD ─────────────────────────────────────────────────────────


async def test_epic_create_get_list(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    e1 = _epic("E-001", ordinal=1)
    e2 = _epic("E-002", ordinal=2)
    await store.create_epic(e1)
    await store.create_epic(e2)

    got = await store.get_epic("E-001")
    assert got is not None
    assert got.title == "Epic E-001"
    assert got.list_status == ArtifactStatus.DRAFT

    listed = await store.list_epics_for_project("proj-bpd-test")
    assert [e.epic_id for e in listed] == ["E-001", "E-002"]


async def test_epic_update_whitelisted(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_epic(_epic("E-010"))
    updated = await store.update_epic("E-010", {
        "title": "Renamed",
        "acceptance_criteria": "new criterion",
        "project_id": "DROPPED",  # not in whitelist — must be ignored
    })
    assert updated.title == "Renamed"
    assert updated.acceptance_criteria == "new criterion"
    assert updated.project_id == "proj-bpd-test"  # unchanged
    assert updated.updated_at is not None


async def test_epic_finalize_archives_other_finalized(tmp_path: Path) -> None:
    """Same shape as project_tasks finalization: flipping v2 to
    finalized should auto-archive the existing finalized v1."""
    store = await _make_store(tmp_path)
    await store.create_epic(_epic("E-v1", version=1, list_status=ArtifactStatus.FINALIZED))
    await store.create_epic(_epic("E-v2", version=2, list_status=ArtifactStatus.DRAFT))
    await store.finalize_epic_list("proj-bpd-test", list_version=2)

    v1 = await store.get_epic("E-v1")
    v2 = await store.get_epic("E-v2")
    assert v1 is not None and v1.list_status == ArtifactStatus.ARCHIVED
    assert v2 is not None and v2.list_status == ArtifactStatus.FINALIZED


async def test_epic_delete_cascade_semantics(tmp_path: Path) -> None:
    """Deleting an epic:
      - hard-deletes UNSTARTED backlog tasks (regenerable draft rows
        that would otherwise pile up as orphans across re-runs)
      - PRESERVES dispatched / in-flight / completed tasks (real work
        history that the user already paid compute for), NULLing the
        feature_id back-link so they survive as "Legacy" rows.
    Previously the cascade NULLed every task indiscriminately, which
    let backlog orphans accumulate across every BPD re-generation
    cycle — they showed in the rollup counter but couldn't render
    under any feature because their feature was gone."""
    store = await _make_store(tmp_path)
    await store.create_epic(_epic("E-X"))
    await store.create_feature(_feature("F-X1", "E-X"))
    await store.create_feature(_feature("F-X2", "E-X"))
    # T-X1: backlog + no request → should be HARD DELETED
    await store.create_task(_task("T-X1", feature_id="F-X1"))
    # T-X2: in_progress with request_id → should SURVIVE with
    # feature_id NULLed (history preserved)
    await store.create_task(_task(
        "T-X2", feature_id="F-X2",
        status=TaskStatus.IN_PROGRESS,
        request_id="REQ-XYZ",
    ))

    await store.delete_epic("E-X")

    assert await store.get_epic("E-X") is None
    assert await store.get_feature("F-X1") is None
    assert await store.get_feature("F-X2") is None
    # Backlog orphan → gone
    assert await store.get_task("T-X1") is None
    # Dispatched task → survives, feature_id NULLed
    t2 = await store.get_task("T-X2")
    assert t2 is not None
    assert t2.feature_id is None
    assert t2.request_id == "REQ-XYZ"  # history preserved


# ── Feature CRUD ──────────────────────────────────────────────────────


async def test_feature_create_with_depends_on(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_epic(_epic("E-001"))
    await store.create_feature(_feature(
        "F-001", "E-001",
        depends_on=["F-other-1", "F-other-2"],
    ))
    got = await store.get_feature("F-001")
    assert got is not None
    assert got.depends_on == ["F-other-1", "F-other-2"]


async def test_feature_list_by_epic(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_epic(_epic("E-A"))
    await store.create_epic(_epic("E-B"))
    await store.create_feature(_feature("F-A1", "E-A", ordinal=1))
    await store.create_feature(_feature("F-A2", "E-A", ordinal=2))
    await store.create_feature(_feature("F-B1", "E-B"))

    listed_a = await store.list_features_for_epic("E-A")
    assert [f.feature_id for f in listed_a] == ["F-A1", "F-A2"]


async def test_feature_delete_cascade_semantics(tmp_path: Path) -> None:
    """Same delete-vs-preserve rules as delete_epic: unstarted backlog
    rows are hard-deleted; dispatched / completed rows survive."""
    store = await _make_store(tmp_path)
    await store.create_epic(_epic("E-001"))
    await store.create_feature(_feature("F-001", "E-001"))
    # Backlog → deleted
    await store.create_task(_task("T-100", feature_id="F-001"))
    # Completed with request → preserved
    await store.create_task(_task(
        "T-101", feature_id="F-001",
        status=TaskStatus.DEPLOYED,
        request_id="REQ-ABC",
    ))
    await store.delete_feature("F-001")
    assert await store.get_feature("F-001") is None
    assert await store.get_task("T-100") is None  # backlog orphan purged
    t = await store.get_task("T-101")
    assert t is not None and t.feature_id is None
    assert t.request_id == "REQ-ABC"


# ── ProjectTask BPD fields round-trip ─────────────────────────────────


async def test_project_task_bpd_fields_roundtrip(tmp_path: Path) -> None:
    """All 5 BPD fields persist + read back cleanly."""
    store = await _make_store(tmp_path)
    await store.create_epic(_epic("E-001"))
    await store.create_feature(_feature("F-001", "E-001"))
    await store.create_task(_task(
        "T-001",
        feature_id="F-001",
        depends_on=["T-blocker-a", "T-blocker-b"],
        primary_file="backend/app/api/v1/dashboard.py",
        expected_loc=180,
        acceptance_test="GET /dashboard/summary returns 200 with kpis array",
    ))
    got = await store.get_task("T-001")
    assert got is not None
    assert got.feature_id == "F-001"
    assert got.depends_on == ["T-blocker-a", "T-blocker-b"]
    assert got.primary_file == "backend/app/api/v1/dashboard.py"
    assert got.expected_loc == 180
    assert got.acceptance_test.startswith("GET /dashboard/summary")


async def test_legacy_task_without_bpd_fields_loads_defaults(tmp_path: Path) -> None:
    """A task created without any BPD fields reads back with NULL /
    empty defaults — matches the legacy-row contract from BPD-401."""
    store = await _make_store(tmp_path)
    legacy = ProjectTask(
        task_id="T-legacy",
        project_id="proj-bpd-test",
        list_version=1,
        list_status=ArtifactStatus.FINALIZED,
        ordinal=1,
        title="legacy task with no BPD fields",
        task_status=TaskStatus.BACKLOG,
        # NO feature_id, depends_on, primary_file, expected_loc, acceptance_test
    )
    await store.create_task(legacy)
    got = await store.get_task("T-legacy")
    assert got is not None
    assert got.feature_id is None
    assert got.depends_on == []
    assert got.primary_file is None
    assert got.expected_loc is None
    assert got.acceptance_test is None


async def test_update_task_can_modify_depends_on(tmp_path: Path) -> None:
    """Inline DAG-edit support (BPD-308) — user can patch depends_on."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-edit", depends_on=["T-old"]))
    updated = await store.update_task("T-edit", {"depends_on": ["T-new1", "T-new2"]})
    assert updated.depends_on == ["T-new1", "T-new2"]


# ── Dependency graph: get_task_blockers ───────────────────────────────


async def test_get_task_blockers_returns_blockers(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-A", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-B", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-C", depends_on=["T-A", "T-B"]))
    blockers = await store.get_task_blockers("T-C")
    assert {b.task_id for b in blockers} == {"T-A", "T-B"}


async def test_get_task_blockers_drops_dangling(tmp_path: Path) -> None:
    """If depends_on references a deleted/nonexistent task, the
    helper silently drops it from the result. Caller compares len()
    to detect the dangling case."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-A", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-X", depends_on=["T-A", "T-nonexistent"]))
    blockers = await store.get_task_blockers("T-X")
    assert {b.task_id for b in blockers} == {"T-A"}


async def test_get_task_blockers_empty_for_no_deps(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-A"))  # depends_on=[]
    blockers = await store.get_task_blockers("T-A")
    assert blockers == []


# ── Dependency graph: get_dispatchable_tasks ─────────────────────────


async def test_dispatchable_includes_no_dep_backlog(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-1"))  # backlog, no deps
    await store.create_task(_task("T-2"))  # backlog, no deps
    ready = await store.get_dispatchable_tasks("proj-bpd-test")
    assert {t.task_id for t in ready} == {"T-1", "T-2"}


async def test_dispatchable_excludes_non_backlog(tmp_path: Path) -> None:
    """A task that's already in_progress / deployed / failed is NOT
    re-dispatchable from this helper."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-1", status=TaskStatus.IN_PROGRESS))
    await store.create_task(_task("T-2", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-3", status=TaskStatus.FAILED))
    await store.create_task(_task("T-4", status=TaskStatus.BACKLOG))
    ready = await store.get_dispatchable_tasks("proj-bpd-test")
    assert [t.task_id for t in ready] == ["T-4"]


async def test_dispatchable_excludes_blocked_tasks(tmp_path: Path) -> None:
    """T-3 depends on T-2 which isn't deployed → T-3 is blocked.
    After T-2 deploys, T-3 becomes dispatchable."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-1", status=TaskStatus.DEPLOYED))
    await store.create_task(_task("T-2", status=TaskStatus.BACKLOG))
    await store.create_task(_task("T-3", depends_on=["T-2"]))

    ready_a = await store.get_dispatchable_tasks("proj-bpd-test")
    assert {t.task_id for t in ready_a} == {"T-2"}  # T-3 blocked by backlog T-2

    # Simulate T-2 deploying
    await store.set_task_status("T-2", TaskStatus.DEPLOYED)
    ready_b = await store.get_dispatchable_tasks("proj-bpd-test")
    assert {t.task_id for t in ready_b} == {"T-3"}  # now unblocked


async def test_dispatchable_ignores_draft_list_status(tmp_path: Path) -> None:
    """Tasks in a draft list aren't dispatchable — must be finalized."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-draft", list_status=ArtifactStatus.DRAFT))
    await store.create_task(_task("T-final", list_status=ArtifactStatus.FINALIZED))
    ready = await store.get_dispatchable_tasks("proj-bpd-test")
    assert [t.task_id for t in ready] == ["T-final"]


async def test_dispatchable_dangling_dep_treated_as_blocker(tmp_path: Path) -> None:
    """If depends_on references a task that doesn't exist, treat the
    dep as unmet (safer than ignoring it). Surfaces dangling refs as
    blocked tasks the user can investigate."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-X", depends_on=["T-ghost"]))
    ready = await store.get_dispatchable_tasks("proj-bpd-test")
    assert ready == []


# ── Dependency graph: has_task_cycle ──────────────────────────────────


async def test_no_cycle_in_linear_dag(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-1"))
    await store.create_task(_task("T-2", depends_on=["T-1"]))
    await store.create_task(_task("T-3", depends_on=["T-2"]))
    await store.create_task(_task("T-4", depends_on=["T-3"]))
    has_cycle, path = await store.has_task_cycle("proj-bpd-test", 1)
    assert has_cycle is False
    assert path == []


async def test_no_cycle_in_branching_dag(tmp_path: Path) -> None:
    """Diamond: T-1 → T-2, T-1 → T-3, T-2 → T-4, T-3 → T-4.
    No cycle even though T-1 has two paths to T-4."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-1"))
    await store.create_task(_task("T-2", depends_on=["T-1"]))
    await store.create_task(_task("T-3", depends_on=["T-1"]))
    await store.create_task(_task("T-4", depends_on=["T-2", "T-3"]))
    has_cycle, _ = await store.has_task_cycle("proj-bpd-test", 1)
    assert has_cycle is False


async def test_self_cycle_detected(tmp_path: Path) -> None:
    """T depends on itself — the most degenerate cycle."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-loop", depends_on=["T-loop"]))
    has_cycle, path = await store.has_task_cycle("proj-bpd-test", 1)
    assert has_cycle is True
    assert "T-loop" in path


async def test_three_node_cycle_detected(tmp_path: Path) -> None:
    """T-1 → T-2 → T-3 → T-1 (loop back)."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-1", depends_on=["T-3"]))
    await store.create_task(_task("T-2", depends_on=["T-1"]))
    await store.create_task(_task("T-3", depends_on=["T-2"]))
    has_cycle, path = await store.has_task_cycle("proj-bpd-test", 1)
    assert has_cycle is True
    # Path should mention all 3 nodes (order varies by start node)
    assert {"T-1", "T-2", "T-3"}.issubset(set(path))


async def test_dangling_dep_is_not_a_cycle(tmp_path: Path) -> None:
    """Depending on a nonexistent task is INVALID but not a CYCLE
    (the cycle detector just skips dangling refs). Cycle detection
    and dangling-ref detection are separate concerns."""
    store = await _make_store(tmp_path)
    await store.create_task(_task("T-1", depends_on=["T-ghost-A"]))
    await store.create_task(_task("T-2", depends_on=["T-ghost-B"]))
    has_cycle, _ = await store.has_task_cycle("proj-bpd-test", 1)
    assert has_cycle is False
