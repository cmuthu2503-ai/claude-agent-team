"""HAI-66 — finalize gated actions + next-wave task reads.

Covers:
  - get_next_wave_tasks: the "one dependency layer ahead" semantics
  - the five finalize action_types are gated AND have a registered handler
"""

from __future__ import annotations

from pathlib import Path

from src.core.proposal_handlers import _HANDLERS
from src.core.proposal_registry import GATED_ACTION_TYPES
from src.models.base import (
    ArtifactStatus,
    Project,
    ProjectStatus,
    ProjectTask,
    TaskStatus,
)
from src.state.sqlite_store import SQLiteStateStore

_FINALIZE_ACTIONS = {
    "prd.finalize",
    "apispec.finalize",
    "tasks.finalize",
    "epics.finalize",
    "features.finalize",
}


async def _store(tmp_path: Path) -> SQLiteStateStore:
    db = SQLiteStateStore(str(tmp_path / "hai66.db"))
    await db.initialize()
    await db.create_project(
        Project(project_id="proj-1", name="Atlas", status=ProjectStatus.ACTIVE)
    )
    return db


def _task(
    task_id: str,
    depends_on: list[str] | None = None,
    status: TaskStatus = TaskStatus.BACKLOG,
    list_status: ArtifactStatus = ArtifactStatus.FINALIZED,
    ordinal: int = 1,
) -> ProjectTask:
    return ProjectTask(
        task_id=task_id,
        project_id="proj-1",
        list_version=1,
        list_status=list_status,
        ordinal=ordinal,
        title=f"Task {task_id}",
        task_status=status,
        depends_on=depends_on or [],
    )


# ── get_next_wave_tasks ──────────────────────────────────────────────────────

async def test_next_wave_is_one_layer_ahead(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    # X already deployed; A startable now (no deps); D startable now (dep X deployed)
    await store.create_task(_task("X", status=TaskStatus.DEPLOYED, ordinal=1))
    await store.create_task(_task("A", ordinal=2))                       # startable now
    await store.create_task(_task("D", depends_on=["X"], ordinal=3))     # startable now
    await store.create_task(_task("B", depends_on=["A"], ordinal=4))     # next wave (A is current)
    await store.create_task(_task("E", depends_on=["A", "X"], ordinal=5))  # next wave (A current, X deployed)
    await store.create_task(_task("C", depends_on=["B"], ordinal=6))     # two layers out → not yet

    startable = {t.task_id for t in await store.get_dispatchable_tasks("proj-1")}
    next_wave = {t.task_id for t in await store.get_next_wave_tasks("proj-1")}

    assert startable == {"A", "D"}
    assert next_wave == {"B", "E"}        # exactly the tasks unblocked once {A,D} deploy
    assert "C" not in next_wave           # still blocked by B (which isn't startable yet)
    assert startable.isdisjoint(next_wave)  # the two waves never overlap


async def test_next_wave_excludes_unfinalized_and_non_backlog(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    await store.create_task(_task("A", ordinal=1))                                  # startable
    # draft (not finalized) dependent → never startable/next-wave
    await store.create_task(_task("B", depends_on=["A"], list_status=ArtifactStatus.DRAFT, ordinal=2))
    # already in-progress dependent → not backlog, excluded
    await store.create_task(_task("C", depends_on=["A"], status=TaskStatus.IN_PROGRESS, ordinal=3))

    assert {t.task_id for t in await store.get_next_wave_tasks("proj-1")} == set()


# ── finalize actions are gated + handled ─────────────────────────────────────

def test_finalize_actions_are_gated() -> None:
    assert _FINALIZE_ACTIONS <= GATED_ACTION_TYPES


def test_finalize_actions_have_handlers() -> None:
    assert _FINALIZE_ACTIONS <= set(_HANDLERS)
