"""Regression test for the DELETE-request cascade fix.

Pins the fix that closed REQ-FEC71B's dangling-pointer pattern. When
a request is deleted via DELETE /api/v1/requests/:id, the cascade now
ALSO sets ``project_tasks.request_id`` to NULL for any task that
pointed at the deleted request. Previously the task row would survive
with a back-link to a row that no longer existed — the task popup
loaded but couldn't fetch the request, and the agent timeline /
commit info silently went blank.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models.base import (
    ArtifactStatus, ProjectTask, Request, TaskStatus,
)
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path: Path) -> SQLiteStateStore:
    db = SQLiteStateStore(str(tmp_path / "test.db"))
    await db.initialize()
    return db


async def test_delete_request_nulls_project_task_back_link(
    store: SQLiteStateStore, tmp_path: Path,
) -> None:
    """The end-to-end repro of REQ-FEC71B's dangling-pointer:

    1. Create a project + task that's been dispatched (request_id set).
    2. Create the matching Request row.
    3. Delete the Request.
    4. The task's request_id MUST be NULL afterwards.
    """
    # Set up minimal fixtures
    from src.models.base import Project, ProjectStatus
    proj = Project(
        project_id="proj-test-cascade",
        name="Test Project",
        status=ProjectStatus.ACTIVE,
    )
    await store.create_project(proj)

    req = Request(
        request_id="REQ-CASCADE-X",
        description="test request",
        task_type="feature_request",
        priority="medium",
        status="failed",
        project_id="proj-test-cascade",
        source_task_id="T-cascade",
    )
    await store.create_request(req)

    # A task that points at the request — the dangling-pointer setup
    task = ProjectTask(
        task_id="T-cascade",
        project_id="proj-test-cascade",
        list_version=1,
        list_status=ArtifactStatus.FINALIZED,
        ordinal=1,
        title="cascade test",
        description="",
        task_type="feature_request",
        priority="medium",
        estimated_agent=None,
        task_status=TaskStatus.FAILED,
        request_id="REQ-CASCADE-X",
    )
    await store.create_task(task)

    # Verify setup
    task_before = await store.get_task("T-cascade")
    assert task_before is not None
    assert task_before.request_id == "REQ-CASCADE-X"

    # Delete the request
    await store.delete_request("REQ-CASCADE-X")

    # The request row should be gone
    assert await store.get_request("REQ-CASCADE-X") is None

    # The task survives BUT its back-link is now NULL — the fix.
    task_after = await store.get_task("T-cascade")
    assert task_after is not None, "task should NOT be deleted by request delete"
    assert task_after.request_id is None, (
        "request_id back-link should be NULL after cascade — "
        f"got {task_after.request_id!r}"
    )
    # task_status is preserved (the user still sees that it was dispatched once)
    assert task_after.task_status == TaskStatus.FAILED


async def test_delete_request_idempotent_when_no_task_points_to_it(
    store: SQLiteStateStore,
) -> None:
    """If no project_task references the request, the cascade is a
    harmless no-op on that table — no exceptions, no orphan errors."""
    from src.models.base import Project, ProjectStatus
    await store.create_project(Project(
        project_id="proj-test-no-task",
        name="x",
        status=ProjectStatus.ACTIVE,
    ))
    req = Request(
        request_id="REQ-NO-TASK",
        description="lone request",
        task_type="feature_request",
        priority="medium",
        status="completed",
        project_id="proj-test-no-task",
        source_task_id=None,
    )
    await store.create_request(req)
    # Delete — must not raise even with no project_tasks pointer
    await store.delete_request("REQ-NO-TASK")
    assert await store.get_request("REQ-NO-TASK") is None


async def test_delete_request_only_nulls_matching_back_links(
    store: SQLiteStateStore,
) -> None:
    """When TWO tasks exist — one pointing at the deleted request, one
    pointing elsewhere — only the matching task's back-link is cleared.
    The unrelated task's request_id is preserved."""
    from src.models.base import Project, ProjectStatus
    await store.create_project(Project(
        project_id="proj-multi",
        name="x",
        status=ProjectStatus.ACTIVE,
    ))
    # Two requests
    for rid in ("REQ-DEL-ME", "REQ-KEEP-ME"):
        await store.create_request(Request(
            request_id=rid,
            description=f"req {rid}",
            task_type="feature_request",
            priority="medium",
            status="completed",
            project_id="proj-multi",
        ))
    # Two tasks
    for tid, rid in (("T-a", "REQ-DEL-ME"), ("T-b", "REQ-KEEP-ME")):
        await store.create_task(ProjectTask(
            task_id=tid, project_id="proj-multi", list_version=1,
            list_status=ArtifactStatus.FINALIZED, ordinal=1,
            title=tid, description="", task_type="feature_request",
            priority="medium", estimated_agent=None,
            task_status=TaskStatus.DEPLOYED, request_id=rid,
        ))
    # Delete one request
    await store.delete_request("REQ-DEL-ME")
    # Affected task's back-link cleared
    t_a = await store.get_task("T-a")
    assert t_a is not None and t_a.request_id is None
    # Unrelated task UNCHANGED
    t_b = await store.get_task("T-b")
    assert t_b is not None and t_b.request_id == "REQ-KEEP-ME"
