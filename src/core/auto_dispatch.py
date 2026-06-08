"""BPD-24 — auto-dispatch handler.

When a Request linked to a project task hits ``deployed`` (i.e. the
underlying task just landed in production), look up the project's
``auto_dispatch_on_deploy`` flag. If True, recompute the set of
dispatchable tasks (BPD-08's ``get_dispatchable_tasks``) and fire any
that became newly-unblocked.

The handler emits ``project.tasks.auto_dispatched`` (BPD-26) with the
list of fired task_ids so the UI's WebSocket subscribers can show the
cascade live. Failures are logged but never bubble up to the event
emitter — a broken auto-dispatch should NOT take down event
broadcasting for the rest of the system.

Registration: ``src/main.py``'s lifespan calls
``events.on(make_auto_dispatch_handler(...))`` alongside the existing
project_task_status handler. The handler depends on the orchestrator
(to ``submit()`` new Requests) and the state store (to read the
project flag + dispatchable set).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from src.core.in_process_gate import submit_gated_action
from src.models.base import ArtifactStatus, TaskStatus
from src.state.base import StateStore

logger = structlog.get_logger()


def make_auto_dispatch_handler(
    state: StateStore, orchestrator, events, governed: Callable[[], bool] | None = None
):
    """Build an EventEmitter-compatible handler bound to the state store
    + orchestrator + events. Closure rather than class — matches the
    pattern of ``make_project_task_status_handler``.

    ``governed`` is a live predicate (HAI-46): when it returns True the unblocked
    set becomes a task.dispatch PROPOSAL instead of an immediate dispatch. Defaults
    to legacy (never governed), so existing call sites/tests are unchanged."""
    _is_governed: Callable[[], bool] = governed if callable(governed) else (lambda: False)

    async def handler(event_type: str, data: dict[str, Any]) -> None:
        # Only act on terminal `deployed` transitions of project tasks.
        # We listen on both `request.completed` (treated as deployed)
        # and `request.status_changed` with status='completed'/'deployed'
        # so we don't depend on a specific emitter convention.
        if event_type not in (
            "request.completed",
            "request.status_changed",
        ):
            return

        request_id = data.get("request_id")
        if not request_id:
            return

        # Filter to events that genuinely represent a deploy.
        if event_type == "request.status_changed":
            status = (data.get("status") or "").lower()
            if status not in ("completed", "deployed"):
                return

        # Look up the source task to discover which project this belongs
        # to. If no source_task_id, this Request wasn't created from a
        # project task (it's a one-off Submit Request) — nothing to do.
        try:
            req = await state.get_request(request_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_dispatch_request_lookup_failed", error=str(e))
            return
        if req is None or not req.source_task_id:
            return
        project_id = req.project_id
        if not project_id:
            return

        # Read the project's toggle. Off by default per BPD §2.3.
        try:
            project = await state.get_project(project_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "auto_dispatch_project_lookup_failed",
                project_id=project_id, error=str(e),
            )
            return
        if project is None or not project.auto_dispatch_on_deploy:
            return

        # Recompute the dispatchable set. This already filters to
        # backlog + finalized + deps-satisfied; nothing else needed.
        try:
            ready = await state.get_dispatchable_tasks(project_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "auto_dispatch_ready_lookup_failed",
                project_id=project_id, error=str(e),
            )
            return
        if not ready:
            return

        # Filter to genuinely dispatchable tasks (fresh backlog + finalized) once,
        # defensively skipping anything a concurrent dispatch already picked up.
        to_dispatch = []
        for task in ready:
            fresh = await state.get_task(task.task_id)
            if fresh is None or fresh.task_status != TaskStatus.BACKLOG:
                continue
            if fresh.list_status != ArtifactStatus.FINALIZED:
                continue
            to_dispatch.append(fresh)
        if not to_dispatch:
            return

        async def _legacy_dispatch():
            """The pre-integration behavior: submit each task directly + emit the
            cascade event. Runs inline in legacy mode; in governed mode this is what
            the task.dispatch proposal's handler does on confirm instead."""
            fired: list[dict[str, str]] = []
            failures: list[dict[str, str]] = []
            for task in to_dispatch:
                try:
                    new_req = await orchestrator.submit(
                        description=task.description or task.title,
                        task_type=task.task_type,
                        priority=task.priority,
                        created_by="auto-dispatch",
                        project_id=project_id,
                        source_task_id=task.task_id,
                    )
                    await state.set_task_status(
                        task.task_id, TaskStatus.DISPATCHED, request_id=new_req.request_id,
                    )
                    fired.append({"task_id": task.task_id, "request_id": new_req.request_id})
                except Exception as e:  # noqa: BLE001
                    failures.append({"task_id": task.task_id, "error": str(e)})

            if fired:
                logger.info(
                    "project_tasks_auto_dispatched",
                    project_id=project_id,
                    trigger_request_id=request_id,
                    trigger_task_id=req.source_task_id,
                    fired_count=len(fired),
                    fired=fired,
                    failures=failures or None,
                )
                try:  # BPD-26 — broadcast for the UI to render the cascade.
                    await events.emit("project.tasks.auto_dispatched", {
                        "project_id": project_id,
                        "trigger_request_id": request_id,
                        "trigger_task_id": req.source_task_id,
                        "fired": fired,
                        "failures": failures or None,
                    })
                except Exception as e:  # noqa: BLE001 — emit must never block
                    logger.warning("auto_dispatch_event_emit_failed", error=str(e))
            return fired

        # HAI-46 — route through the approval gate. Governed mode turns the whole
        # unblocked set into ONE task.dispatch proposal (a human confirms it, then
        # the HAI-41 handler dispatches) + a notification; legacy mode runs
        # _legacy_dispatch inline (unchanged). Idempotent per (project, trigger) so a
        # re-emitted trigger can't queue duplicate proposals.
        outcome = await submit_gated_action(
            state, events,
            governed=_is_governed(),
            action_type="task.dispatch",
            proposed_by="system:auto-dispatch",
            target_ref=project_id,
            payload={
                "project_id": project_id,
                "task_ids": [t.task_id for t in to_dispatch],
                "trigger_request_id": request_id,
                "trigger_task_id": req.source_task_id,
            },
            idempotency_key=f"autodispatch:{project_id}:{request_id}",
            execute=_legacy_dispatch,
        )
        if outcome.gated:
            logger.info(
                "project_tasks_dispatch_proposed",
                project_id=project_id,
                proposal_id=outcome.proposal_id,
                task_count=len(to_dispatch),
            )
            try:
                await events.emit("project.tasks.dispatch_proposed", {
                    "project_id": project_id,
                    "proposal_id": outcome.proposal_id,
                    "task_ids": [t.task_id for t in to_dispatch],
                    "trigger_request_id": request_id,
                })
            except Exception as e:  # noqa: BLE001 — emit must never block
                logger.warning("dispatch_proposed_emit_failed", error=str(e))

    return handler
