"""Project-driven Build · chat with project_orchestrator (PDB-35 + PDB-37).

Houses the per-project chat session: bound tools (list_tasks, dispatch_task,
cancel_task, get_project_status, modify_task, add_task) and the tool-use
loop that runs `messages.create()` until the agent produces a text reply.

Separated from `src/api/routes/projects.py` so the route stays focused on
HTTP plumbing.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

import structlog

from src.models.base import (
    ArtifactStatus,
    BuildMessage,
    ProjectTask,
    TaskStatus,
)
from src.state.base import StateStore

logger = structlog.get_logger()


# ── Tool schemas (Anthropic Messages API tool-use shape) ─────────────────
# These are the schemas surfaced to the LLM. The route binds project_id
# server-side — it's NOT a tool argument the LLM can spoof.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_tasks",
        "description": (
            "Return every task in this project's current task list, with "
            "title, type, priority, current task_status, and (if dispatched) "
            "the request_id. Call this BEFORE answering any question about "
            "progress or what's left to do."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "dispatch_task",
        "description": (
            "Dispatch a backlog task — creates a Request that runs the per-task "
            "workflow. Returns {task_id, request_id, status}. Idempotent: "
            "re-dispatching an already-dispatched task echoes the existing "
            "request_id with status='already_dispatched'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "e.g. T-abc12345"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "cancel_task",
        "description": (
            "Cancel a dispatched task. Sets its task_status to 'cancelled'. "
            "Only works if the task isn't already in a terminal state "
            "(deployed/failed/cancelled). If a Request is in flight, it is "
            "marked cancelled too. Returns {task_id, status}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "get_project_status",
        "description": (
            "Return counts of tasks by task_status (backlog, in_progress, "
            "review, testing, deployed, failed, cancelled) plus list_version "
            "and total count. Use this for high-level progress questions."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "modify_task",
        "description": (
            "Patch a single task's editable fields. Pass only the fields you "
            "want to change. Marks the task amended=true for audit. "
            "Allowed field keys: title, description, task_type, priority, "
            "estimated_agent. Values are validated server-side. Returns "
            "the updated task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": "Map of field name → new value. Unknown keys are silently ignored.",
                },
            },
            "required": ["task_id", "fields"],
        },
    },
    {
        "name": "add_task",
        "description": (
            "Append a new task to the current finalized list. Marks amended=true. "
            "Returns the new task_id. priority defaults to 'medium', task_type "
            "to 'feature_request', estimated_agent to null if omitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "task_type": {"type": "string"},
                "priority": {"type": "string"},
                "estimated_agent": {"type": "string"},
            },
            "required": ["title"],
        },
    },
]


_VALID_TASK_TYPES = {
    "feature_request", "bug_report", "doc_request",
    "demo_request", "research_request", "content_request",
}
_VALID_PRIORITIES = {"low", "medium", "high"}


class BuildTools:
    """Project-bound tool implementations. Constructed per chat turn so
    `project_id` is closed over and the LLM can't spoof it."""

    def __init__(self, project_id: str, state: StateStore, orchestrator: Any) -> None:
        self.project_id = project_id
        self.state = state
        self.orchestrator = orchestrator

    async def _current_list_version(self) -> int | None:
        """Returns the project's current finalized list_version, or None
        if there isn't one. Used by add_task to know where to append."""
        rows = await self.state.list_tasks_for_project(
            self.project_id, list_status=ArtifactStatus.FINALIZED,
        )
        return rows[0].list_version if rows else None

    async def list_tasks(self) -> str:
        rows = await self.state.list_tasks_for_project(self.project_id)
        return json.dumps([_task_compact(t) for t in rows])

    async def dispatch_task(self, task_id: str) -> str:
        task = await self.state.get_task(task_id)
        if task is None or task.project_id != self.project_id:
            return json.dumps({"error": f"Task {task_id!r} not found in this project."})
        if task.list_status != ArtifactStatus.FINALIZED:
            # !s on the StrEnum yields the plain "draft" / "archived"
            # value; bare !r would render the Python repr
            # ("<ArtifactStatus.DRAFT: 'draft'>") which leaks internals
            # into the chat surface. Same applies to task_id — quoted
            # bare value reads cleaner than a Python string repr.
            return json.dumps({
                "error": (
                    f"Task '{task_id}' is in a {task.list_status} list — only "
                    f"finalized tasks can be dispatched. Ask the user to click "
                    f"'Finalize Tasks' in the task list panel first; do NOT "
                    f"retry dispatch_task on other tasks in the same list — "
                    f"they will all fail the same way."
                ),
                "list_status": str(task.list_status),
                "remedy": "finalize_task_list",
            })
        # Idempotency (matches the BLD-004 contract on the dispatch route).
        if task.task_status != TaskStatus.BACKLOG and task.request_id:
            return json.dumps({
                "task_id": task_id,
                "request_id": task.request_id,
                "status": "already_dispatched",
            })
        try:
            req = await self.orchestrator.submit(
                description=task.description or task.title,
                task_type=task.task_type,
                priority=task.priority,
                created_by="project_orchestrator",
                project_id=self.project_id,
                source_task_id=task_id,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        await self.state.set_task_status(task_id, TaskStatus.DISPATCHED, request_id=req.request_id)
        return json.dumps({
            "task_id": task_id,
            "request_id": req.request_id,
            "status": "dispatched",
        })

    async def cancel_task(self, task_id: str) -> str:
        task = await self.state.get_task(task_id)
        if task is None or task.project_id != self.project_id:
            return json.dumps({"error": f"Task {task_id!r} not found in this project."})
        if task.task_status in {TaskStatus.DEPLOYED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return json.dumps({
                "task_id": task_id,
                "status": str(task.task_status),
                "note": "already in terminal state",
            })
        # If there's an in-flight Request, mark it cancelled too.
        if task.request_id:
            try:
                req = await self.state.get_request(task.request_id)
                if req:
                    from src.models.base import RequestStatus
                    req.status = RequestStatus.CANCELLED
                    req.completed_at = datetime.utcnow()
                    await self.state.update_request(req)
            except Exception as e:
                logger.warning("cancel_request_failed", task_id=task_id, error=str(e))
        await self.state.set_task_status(task_id, TaskStatus.CANCELLED)
        return json.dumps({"task_id": task_id, "status": "cancelled"})

    async def get_project_status(self) -> str:
        rows = await self.state.list_tasks_for_project(self.project_id)
        counts: dict[str, int] = {}
        latest_update: datetime | None = None
        for t in rows:
            counts[str(t.task_status)] = counts.get(str(t.task_status), 0) + 1
            if t.updated_at and (latest_update is None or t.updated_at > latest_update):
                latest_update = t.updated_at
        return json.dumps({
            "project_id": self.project_id,
            "list_version": rows[0].list_version if rows else None,
            "list_status": str(rows[0].list_status) if rows else None,
            "total": len(rows),
            "counts": counts,
            "latest_update_at": latest_update.isoformat() if latest_update else None,
        })

    async def modify_task(self, task_id: str, fields: dict[str, Any]) -> str:
        task = await self.state.get_task(task_id)
        if task is None or task.project_id != self.project_id:
            return json.dumps({"error": f"Task {task_id!r} not found in this project."})
        # Validate the fields the same way the HTTP PATCH route does.
        clean: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "title" and isinstance(v, str) and v.strip():
                clean["title"] = v.strip()[:200]
            elif k == "description" and isinstance(v, str):
                clean["description"] = v[:2000]
            elif k == "task_type" and v in _VALID_TASK_TYPES:
                clean["task_type"] = v
            elif k == "priority" and v in _VALID_PRIORITIES:
                clean["priority"] = v
            elif k == "estimated_agent":
                clean["estimated_agent"] = v or None
        if not clean:
            return json.dumps({"error": "No valid fields supplied. Allowed: title, description, task_type, priority, estimated_agent."})
        clean["amended"] = True
        updated = await self.state.update_task(task_id, clean)
        return json.dumps({"task_id": task_id, "updated_fields": list(clean.keys()), "task": _task_compact(updated)})

    async def add_task(
        self,
        title: str,
        description: str = "",
        task_type: str = "feature_request",
        priority: str = "medium",
        estimated_agent: str | None = None,
    ) -> str:
        if not title or not title.strip():
            return json.dumps({"error": "title is required."})
        if task_type not in _VALID_TASK_TYPES:
            task_type = "feature_request"
        if priority not in _VALID_PRIORITIES:
            priority = "medium"
        list_version = await self._current_list_version()
        if list_version is None:
            return json.dumps({"error": "No finalized task list exists for this project — finalize one first."})
        # Pick next ordinal.
        existing = await self.state.list_tasks_for_project(self.project_id, list_version=list_version)
        next_ordinal = (max((t.ordinal for t in existing), default=0)) + 1
        new_task = ProjectTask(
            task_id=f"T-{uuid.uuid4().hex[:8]}",
            project_id=self.project_id,
            list_version=list_version,
            list_status=ArtifactStatus.FINALIZED,  # appended directly to the finalized list
            ordinal=next_ordinal,
            title=title.strip()[:200],
            description=description[:2000],
            task_type=task_type,
            priority=priority,
            estimated_agent=estimated_agent or None,
            task_status=TaskStatus.BACKLOG,
            amended=True,
        )
        await self.state.create_task(new_task)
        return json.dumps({"task_id": new_task.task_id, "task": _task_compact(new_task)})

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> tuple[str, str]:
        """Dispatch one tool call. Returns (raw_result_json, summary_string).
        The summary is the short string shown as a UI chip — kept here
        rather than in the route so the wording is testable as a unit."""
        try:
            if tool_name == "list_tasks":
                raw = await self.list_tasks()
                parsed = json.loads(raw)
                summary = f"📋 Listed {len(parsed)} task{'s' if len(parsed) != 1 else ''}"
                return raw, summary
            if tool_name == "dispatch_task":
                raw = await self.dispatch_task(tool_input.get("task_id", ""))
                parsed = json.loads(raw)
                task_id = tool_input.get("task_id", "") or parsed.get("task_id", "")
                if parsed.get("error"):
                    # Special-case the "list still in draft" failure with a
                    # tight, readable chip. The verbose error+remedy stays
                    # in the JSON for the agent to read.
                    if parsed.get("remedy") == "finalize_task_list":
                        summary = f"❌ {task_id} — list in draft (finalize first)"
                    else:
                        # Cap the chip text length so a long error doesn't
                        # blow up the chat layout.
                        msg = parsed["error"]
                        if len(msg) > 90:
                            msg = msg[:87] + "…"
                        summary = f"❌ Dispatch failed: {msg}"
                elif parsed.get("status") == "already_dispatched":
                    summary = f"ℹ️ {parsed['task_id']} already dispatched → {parsed['request_id']}"
                else:
                    summary = f"🚀 Dispatched {parsed.get('task_id')} → {parsed.get('request_id')}"
                return raw, summary
            if tool_name == "cancel_task":
                raw = await self.cancel_task(tool_input.get("task_id", ""))
                parsed = json.loads(raw)
                if parsed.get("error"):
                    summary = f"❌ Cancel failed: {parsed['error']}"
                else:
                    summary = f"⏸️ Cancelled {parsed.get('task_id')}"
                return raw, summary
            if tool_name == "get_project_status":
                raw = await self.get_project_status()
                parsed = json.loads(raw)
                counts = parsed.get("counts", {})
                bits = [f"{k}: {v}" for k, v in counts.items()]
                summary = f"📊 Status — {', '.join(bits) if bits else 'empty'}"
                return raw, summary
            if tool_name == "modify_task":
                raw = await self.modify_task(
                    tool_input.get("task_id", ""),
                    tool_input.get("fields", {}) or {},
                )
                parsed = json.loads(raw)
                if parsed.get("error"):
                    summary = f"❌ Modify failed: {parsed['error']}"
                else:
                    fields = parsed.get("updated_fields", [])
                    summary = f"✏️ Modified {parsed.get('task_id')} ({', '.join(fields)})"
                return raw, summary
            if tool_name == "add_task":
                raw = await self.add_task(
                    title=tool_input.get("title", ""),
                    description=tool_input.get("description", ""),
                    task_type=tool_input.get("task_type", "feature_request"),
                    priority=tool_input.get("priority", "medium"),
                    estimated_agent=tool_input.get("estimated_agent"),
                )
                parsed = json.loads(raw)
                if parsed.get("error"):
                    summary = f"❌ Add failed: {parsed['error']}"
                else:
                    summary = f"➕ Added {parsed.get('task_id')}"
                return raw, summary
        except Exception as e:
            logger.exception("build_tool_failed", tool=tool_name)
            return json.dumps({"error": str(e)}), f"❌ {tool_name} crashed: {e}"
        return json.dumps({"error": f"Unknown tool: {tool_name}"}), f"❌ Unknown tool: {tool_name}"


def _task_compact(t: ProjectTask) -> dict[str, Any]:
    """Slimmed-down dict for tool output — keeps the LLM context lean and
    avoids leaking internal columns like list_status that aren't actionable
    from chat."""
    return {
        "task_id": t.task_id,
        "ordinal": t.ordinal,
        "title": t.title,
        "task_type": t.task_type,
        "priority": t.priority,
        "estimated_agent": t.estimated_agent,
        "task_status": str(t.task_status),
        "request_id": t.request_id,
    }


# ── Chat turn orchestration ──────────────────────────────────────────────


# Hard cap on tool-use iterations per user turn. Five gives the agent room
# to list_tasks → analyze → dispatch a couple of things in one turn, while
# preventing runaway loops if the prompt drifts.
_MAX_TOOL_ITERATIONS = 5
_MAX_HISTORY_MESSAGES = 20  # CHT-005


async def run_chat_turn(
    *,
    state: StateStore,
    executor: Any,
    orchestrator: Any,
    project_id: str,
    user_message: str,
    user_id: str | None,
    events: Any = None,
) -> dict[str, Any]:
    """Append the user's message, run the orchestrator with the tool-use
    loop, persist the assistant turn (+ any tool_calls), and emit
    `project.build.message` so other tabs see the same conversation.

    Returns {assistant_message, tool_calls, message_id}.
    """
    project_orch_agent = executor.registry.get("project_orchestrator")
    if project_orch_agent is None:
        raise RuntimeError("project_orchestrator agent not registered — is it in config/agents/?")

    # Persist the user message FIRST so the history fetch below picks it
    # up. Avoids the awkward "your message disappears for a moment" UX
    # if anything in the agent loop crashes mid-call.
    user_msg = BuildMessage(
        message_id=f"msg-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        role="user",
        content=user_message,
        tool_calls=None,
        created_by=user_id,
    )
    await state.create_message(user_msg)
    if events is not None:
        await events.emit("project.build.message", {
            "project_id": project_id,
            "message_id": user_msg.message_id,
            "role": "user",
            "content_preview": user_message[:140],
        })

    # Build conversation context: last N messages from this project, in
    # chronological order. The Anthropic Messages API wants strict
    # user/assistant alternation, so we collapse 'tool' role rows into
    # the assistant turn they belong to (we don't replay old tool_use
    # blocks — only their textual aftermath).
    history = await state.list_messages_for_project(project_id, limit=_MAX_HISTORY_MESSAGES)

    # tool_results aren't kept across turns — they were one-shot context.
    # Build messages excluding 'tool' rows and the user_msg we just added
    # (we'll append it explicitly at the end so it's always last).
    messages: list[dict[str, Any]] = []
    for m in history:
        if m.message_id == user_msg.message_id:
            continue
        if m.role == "tool":
            continue
        # Skip empty-content rows (e.g. assistant turn that was tool-only
        # then errored before final text). Their tool_calls already happened
        # on the database; replaying them in context is misleading.
        if not (m.content or "").strip():
            continue
        messages.append({
            "role": m.role,
            "content": m.content,
        })
    messages.append({"role": "user", "content": user_message})

    tools = BuildTools(project_id=project_id, state=state, orchestrator=orchestrator)

    # Tool-use loop. We call `_call_anthropic` on the agent directly so we
    # can pass our custom tool_schemas (the agent's own tools list is
    # empty per the YAML — the project tools are bound here).
    aggregated_text: list[str] = []
    tool_call_summaries: list[dict[str, Any]] = []

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        response = await project_orch_agent._call_anthropic(
            messages=messages,
            tool_schemas=TOOL_SCHEMAS,
        )
        text = (response.get("text") or "").strip()
        if text:
            aggregated_text.append(text)

        tool_calls = response.get("tool_calls", [])
        if not tool_calls:
            break  # done — model produced final text

        # Execute each tool call, build tool_result blocks, append to messages.
        messages.append({
            "role": "assistant",
            "content": response["content"],
        })
        tool_results_block = []
        for tc in tool_calls:
            name = tc.get("name", "")
            tc_input = tc.get("input", {}) or {}
            raw, summary = await tools.execute(name, tc_input)
            tool_call_summaries.append({
                "tool": name,
                "input": tc_input,
                "result_summary": summary,
            })
            tool_results_block.append({
                "type": "tool_result",
                "tool_use_id": tc.get("id", ""),
                "content": raw,
            })
        messages.append({
            "role": "user",
            "content": tool_results_block,
        })

    final_text = "\n\n".join(aggregated_text) if aggregated_text else "(no response)"

    # Persist the assistant turn + emit.
    asst_msg = BuildMessage(
        message_id=f"msg-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        role="assistant",
        content=final_text,
        tool_calls=tool_call_summaries or None,
        created_by=None,
    )
    await state.create_message(asst_msg)
    if events is not None:
        await events.emit("project.build.message", {
            "project_id": project_id,
            "message_id": asst_msg.message_id,
            "role": "assistant",
            "content_preview": final_text[:140],
            "tool_count": len(tool_call_summaries),
        })

    return {
        "message_id": asst_msg.message_id,
        "content": final_text,
        "tool_calls": tool_call_summaries,
    }
