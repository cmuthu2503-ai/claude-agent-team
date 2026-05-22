"""Orchestrator — main entry point. Manages request lifecycle end-to-end."""

import asyncio
import random
import uuid
from datetime import datetime
from typing import Any

import structlog

from src.config.loader import ConfigLoader
from src.core.aggregator import Aggregator
from src.core.dispatcher import Dispatcher
from src.core.events import EventEmitter
import re

from src.models.base import AcceptanceCriterion, Document, Request, RequestStatus, Story, StoryStatus, Subtask, SubtaskStatus, TestCase
from src.state.base import StateStore
from src.workflows.loader import WorkflowLoader
from src.workflows.runner import AgentExecutor, WorkflowRunner

logger = structlog.get_logger()


# Matches `path/to/file.py:LINE:COL` style citations inside ruff / tsc /
# pytest error messages. Used to fish the line + col out so the rework
# prompt can include the actual file content at that line.
_FILE_LINE_RE = re.compile(
    r"(?P<path>[\w./\\-]+\.(?:py|ts|tsx|js|jsx)):(?P<line>\d+)(?::(?P<col>\d+))?",
)

# Matches the CodeWriter drop-guard rejection's `Refusing to overwrite 'PATH'`
# preamble. The full message goes "Refusing to overwrite 'frontend/src/index.css':
# line count dropped from 72 to 3 (96% reduction)" — we capture PATH so we can
# embed the FULL current file content into the rework prompt. Without this the
# agent gets the error text but no on-disk visibility, can't tell the system's
# rejection from "I emitted what I intended", and re-emits the same shrink.
_DROP_GUARD_PATH_RE = re.compile(
    r"Refusing to overwrite '(?P<path>[^']+)':\s*line count dropped",
)

# Cap how much of a cited file we embed. 200 lines covers the common case
# (most config + entry-point files are <200 LOC) without ballooning the
# prompt for a 2000-line file. We also clamp total bytes per snippet so
# a single dense minified file can't blow the budget.
_MAX_FULL_FILE_LINES = 200
_MAX_FULL_FILE_BYTES = 8_000


def _enrich_error_with_line_snippets(
    error: str,
    project_root: "Any" = None,  # Path | None — Any to avoid the import here
) -> str:
    """Append the current file content to the error string so the rework
    agent can see what's actually on disk before deciding how to fix it.

    Two complementary modes — both produce a "CURRENT FILE CONTENT" block
    appended to the original error:

    1. **Line-snippet mode** (the original): triggered by ruff / tsc /
       pytest errors that cite ``path/to/file.py:LINE[:COL]``. Reads each
       cited file and embeds a 5-line context window around the cited
       line, with a ``>`` marker on the exact line.

    2. **Full-file mode** (new): triggered by the CodeWriter drop-guard
       rejection (``Refusing to overwrite 'PATH': line count dropped
       from N to M``). The drop guard doesn't cite a line — its complaint
       is about file *shape*, not a specific line — so a 5-line window is
       useless. We embed the FULL current file content (capped at
       ``_MAX_FULL_FILE_LINES`` lines / ``_MAX_FULL_FILE_BYTES`` bytes)
       so the agent can see what it would have clobbered and make an
       informed choice between (a) re-emit a merged version that
       preserves the lines it wants, (b) use ``search_replace`` for the
       intended surgical edit, (c) declare the shrink genuinely
       intentional (which the exempt list in code_writer.py covers
       for known config-seed basenames).

    Soft-fails per-citation. Bounded loops + reads. Never raises.
    """
    if not error or project_root is None:
        return error

    snippets: list[str] = []
    MAX_CITATIONS = 8

    # ── Mode 2: drop-guard rejections ── Process FIRST so the full-file
    # snippets render above the line-window snippets in the prompt (the
    # drop-guard case is the higher-leverage information for the agent).
    seen_full: set[str] = set()
    for m in _DROP_GUARD_PATH_RE.finditer(error):
        rel_path = m.group("path").replace("\\", "/")
        if rel_path in seen_full:
            continue
        seen_full.add(rel_path)
        if len(snippets) >= MAX_CITATIONS:
            break
        try:
            abs_path = (project_root / rel_path).resolve()
            if not str(abs_path).startswith(str(project_root.resolve())):
                continue
            if not abs_path.is_file():
                continue
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = text.splitlines()
        total_lines = len(lines)
        truncated = False
        if total_lines > _MAX_FULL_FILE_LINES:
            lines = lines[:_MAX_FULL_FILE_LINES]
            truncated = True
        rendered_body = "\n".join(
            f"  {i + 1:4d}  {ln}" for i, ln in enumerate(lines)
        )
        if len(rendered_body) > _MAX_FULL_FILE_BYTES:
            rendered_body = rendered_body[:_MAX_FULL_FILE_BYTES] + "\n  ... [byte-cap truncated]"
            truncated = True
        head = f"--- CURRENT ON-DISK CONTENT: {rel_path} ({total_lines} lines{', truncated' if truncated else ''}) ---"
        snippets.append(head + "\n" + rendered_body)

    # ── Mode 1: line-snippet citations ──
    seen_line: set[tuple[str, int]] = set()
    for m in _FILE_LINE_RE.finditer(error):
        if len(snippets) >= MAX_CITATIONS:
            break
        rel_path = m.group("path").replace("\\", "/")
        try:
            line_no = int(m.group("line"))
        except ValueError:
            continue
        key = (rel_path, line_no)
        if key in seen_line:
            continue
        seen_line.add(key)
        try:
            abs_path = (project_root / rel_path).resolve()
            if not str(abs_path).startswith(str(project_root.resolve())):
                continue
            if not abs_path.is_file():
                continue
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = text.splitlines()
        if not lines or line_no < 1 or line_no > len(lines):
            continue
        start = max(0, line_no - 3)
        end = min(len(lines), line_no + 2)
        rendered: list[str] = []
        for i in range(start, end):
            marker = ">" if (i + 1) == line_no else " "
            rendered.append(f"  {marker} {i + 1:4d}  {lines[i]}")
        snippets.append(f"--- {rel_path}:{line_no} ---\n" + "\n".join(rendered))

    if not snippets:
        return error
    # Detect the "agent only fixes the cited line, next sibling line fires
    # next cycle" failure class. When ruff cites E501 (or any line-too-long
    # variant), an alignment table or docstring usually has MORE long lines
    # nearby — fixing only the cited one wastes a full rework cycle.
    # REQ-B55FB8 died this way: 3 cycles, lines 13/15/17 all in the same
    # docstring coverage map.
    e501_warning = ""
    if "E501" in error or "Line too long" in error.lower() or "line too long" in error:
        e501_warning = (
            "\n\n=== IMPORTANT: SCAN THE WHOLE FILE FOR SIMILAR ISSUES ===\n"
            "The error cites one line, but lint failures of this class usually\n"
            "come in clusters (e.g. a docstring alignment table has multiple\n"
            "long lines; a list of parametrize cases has many over-width\n"
            "entries). When you re-emit the file:\n"
            "  • Scan every line for the same violation, not just the cited one.\n"
            "  • Fix ALL of them in this rework cycle.\n"
            "  • If the long lines are docstring content that can't be wrapped\n"
            "    without losing readability (e.g. Sphinx :func: coverage maps),\n"
            "    append `  # noqa: E501` to each one OR move them out of the\n"
            "    docstring into a comment block.\n"
            "MAX_REWORK_CYCLES is 2 — you do not get a 3rd attempt on the\n"
            "same file.\n"
        )
    return (
        error
        + e501_warning
        + "\n\n=== CURRENT FILE CONTENT AT EACH CITED LOCATION ===\n"
        + "(Use this to decide between merging into the existing file, using\n"
        + "`search_replace` for a surgical edit, or re-emitting a full file\n"
        + "that preserves the lines you intended to keep.)\n\n"
        + "\n\n".join(snippets)
    )


# Simulated processing time per agent (seconds) when using mock executor
MOCK_AGENT_DELAYS: dict[str, tuple[float, float]] = {
    "engineering_lead": (1.5, 3.0),
    "prd_specialist": (3.0, 6.0),
    "user_story_author": (2.0, 4.0),
    "code_reviewer": (2.0, 5.0),
    "backend_specialist": (4.0, 8.0),
    "frontend_specialist": (4.0, 8.0),
    "devops_specialist": (2.0, 4.0),
    "tester_specialist": (3.0, 6.0),
}


class Orchestrator(AgentExecutor):
    """Top-level coordinator. Submits requests, runs workflows, aggregates results."""

    def __init__(
        self,
        config: ConfigLoader,
        state: StateStore,
        events: EventEmitter,
    ) -> None:
        self.config = config
        self.state = state
        self.events = events
        self.dispatcher = Dispatcher(config)
        self.aggregator = Aggregator()
        self.workflow_loader = WorkflowLoader(config)
        self.workflow_loader.load_all()
        self.runner = WorkflowRunner(
            executor=self,
            code_commit_handler=self._handle_code_commit,
            publish_handler=self._handle_publish,
            materialize_handler=self._handle_materialize,
        )
        self._agent_executor: Any = None  # Set by agent system in Phase 3
        # Background tasks keyed by request_id so cancel() can find and kill a
        # specific in-flight workflow. A request can only have one running task
        # at a time (submit → one task; retry reuses the same request_id).
        self._background_tasks: dict[str, asyncio.Task] = {}

        # Token tracker for cost recording
        from src.core.token_tracker import TokenTracker
        self._token_tracker = TokenTracker(state)

        # Code writer for Level 3 deployment
        from src.core.code_writer import CodeWriter
        self._code_writer = CodeWriter(state)

        # Research publisher for the research → docs/ + GitHub flow
        from src.core.research_publisher import ResearchPublisher
        self._research_publisher = ResearchPublisher()

    def set_agent_executor(self, executor: Any) -> None:
        """Inject the real agent executor (set during Phase 3 agent system init)."""
        self._agent_executor = executor

    async def submit(self, description: str, task_type: str = "feature_request",
                     priority: str = "medium", created_by: str = "",
                     project_id: str | None = None,
                     source_task_id: str | None = None) -> Request:
        request_id = f"REQ-{uuid.uuid4().hex[:6].upper()}"

        # PM-11 — project assignment. Default to the immutable Unassigned
        # project when caller doesn't supply one. Validate exists + active
        # otherwise; reject with ValueError (route turns into HTTP 400).
        from src.models.base import UNASSIGNED_PROJECT_ID, ProjectStatus
        if not project_id:
            project_id = UNASSIGNED_PROJECT_ID
        else:
            project = await self.state.get_project(project_id)
            if project is None:
                raise ValueError(f"Project {project_id!r} not found.")
            if project.status != ProjectStatus.ACTIVE:
                raise ValueError(f"Project {project_id!r} is archived; cannot file requests against it.")

        request = Request(
            request_id=request_id,
            description=description,
            task_type=task_type,
            priority=priority,
            created_by=created_by,
            project_id=project_id,
            source_task_id=source_task_id,  # PDB-23 — back-link for project-mode Story Board
        )
        await self.state.create_request(request)
        await self.events.emit("request.created", {
            "request_id": request_id, "description": description, "task_type": task_type,
            "project_id": project_id,
            "source_task_id": source_task_id,
        })
        logger.info("request_submitted", request_id=request_id, task_type=task_type,
                   project_id=project_id, source_task_id=source_task_id)

        # Check for existing documents (duplicate detection)
        skip_stages: list[str] = []
        reused_artifacts: dict[str, Any] = {}
        existing_prds = await self.state.search_documents(description, doc_type="prd", limit=3)
        existing_stories = await self.state.search_documents(description, doc_type="user_stories", limit=3)

        if existing_prds:
            best_prd = existing_prds[0]
            keywords = self._extract_keywords(description)
            prd_keywords = self._extract_keywords(best_prd.title + " " + best_prd.content[:500])
            overlap = len(set(keywords) & set(prd_keywords))
            confidence = overlap / max(len(keywords), 1)

            if confidence >= 0.5:  # 50%+ keyword overlap
                reused_artifacts["prd_document"] = best_prd.content
                reused_artifacts["reused_prd_id"] = best_prd.document_id
                reused_artifacts["reused_prd_request"] = best_prd.request_id
                skip_stages.append("requirements")
                logger.info("reusing_prd", document_id=best_prd.document_id,
                           from_request=best_prd.request_id, confidence=f"{confidence:.0%}")
                await self.events.emit("document.reused", {
                    "request_id": request_id, "doc_type": "prd",
                    "from_request": best_prd.request_id, "confidence": f"{confidence:.0%}",
                })

                if existing_stories:
                    best_stories = existing_stories[0]
                    reused_artifacts["user_stories"] = best_stories.content
                    reused_artifacts["reused_stories_id"] = best_stories.document_id
                    skip_stages.append("story_creation")
                    logger.info("reusing_stories", document_id=best_stories.document_id)

        # Update to analyzing immediately
        request.status = RequestStatus.ANALYZING
        await self.state.update_request(request)
        await self.events.emit("request.status_changed", {
            "request_id": request_id, "status": "analyzing",
        })

        # Run the workflow in the background
        task = asyncio.create_task(
            self._run_workflow(request, description, skip_stages, reused_artifacts)
        )
        self._background_tasks[request_id] = task
        # When the task finishes (normal completion, error, or cancel), drop it
        # from the tracking dict so cancel() doesn't try to re-cancel a dead task.
        task.add_done_callback(lambda _t, rid=request_id: self._background_tasks.pop(rid, None))

        return request

    async def cancel(self, request_id: str) -> Request | None:
        """Cancel an in-flight request.

        - Cancels the background asyncio task (if any) running its workflow
        - Marks the request as CANCELLED
        - Marks any IN_PROGRESS / PENDING subtasks as CANCELLED
        - Idempotent: re-cancelling a terminal request is a no-op (returns the
          request unchanged) so the UI doesn't have to gate on status.

        Returns the updated request, or None if the request doesn't exist.
        """
        request = await self.state.get_request(request_id)
        if not request:
            return None

        # Terminal states — nothing to cancel
        if request.status in (
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        ):
            return request

        # Kill the background task if it's still alive. We best-effort wait a
        # short window for it to unwind so its own state writes settle before
        # we overwrite with CANCELLED, but we don't block indefinitely.
        task = self._background_tasks.pop(request_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                # Swallow anything else the task raised during unwind — we
                # care about state, not its exit code.
                logger.debug("cancel_task_unwind_exception", request_id=request_id)

        # Fail any still-in-progress subtasks so the UI doesn't show them spinning
        now = datetime.utcnow()
        for subtask in await self.state.get_subtasks_for_request(request_id):
            if subtask.status in (SubtaskStatus.IN_PROGRESS, SubtaskStatus.PENDING):
                subtask.status = SubtaskStatus.CANCELLED
                subtask.completed_at = now
                subtask.error_message = "Cancelled by user"
                await self.state.update_subtask(subtask)

        # Flip the request itself. Re-fetch so we don't clobber any fields
        # that settled between the get at the top of this method and now.
        fresh = await self.state.get_request(request_id) or request
        fresh.status = RequestStatus.CANCELLED
        fresh.completed_at = now
        await self.state.update_request(fresh)

        await self.events.emit("request.cancelled", {
            "request_id": request_id,
            "cancelled_at": now.isoformat(),
        })
        logger.info("request_cancelled", request_id=request_id)
        return fresh

    async def _run_workflow(
        self, request: Request, description: str,
        skip_stages: list[str] | None = None, reused_artifacts: dict[str, Any] | None = None,
    ) -> None:
        """Execute the workflow pipeline in the background."""
        request_id = request.request_id
        try:
            # Brief pause to let the API response return first
            await asyncio.sleep(0.5)

            # Move to in_progress
            request.status = RequestStatus.IN_PROGRESS
            await self.state.update_request(request)
            await self.events.emit("request.status_changed", {
                "request_id": request_id, "status": "in_progress",
            })

            workflow = self.workflow_loader.get_workflow_for_trigger(request.task_type)
            if not workflow:
                raise ValueError(f"No workflow found for trigger: {request.task_type}")

            # Merge reused documents into initial artifacts
            initial_artifacts = {"description": description}
            if reused_artifacts:
                initial_artifacts.update(reused_artifacts)

            # ── Retire per-task DevOps stage for project work ──
            # When a Request belongs to a Project, the AI Deploy Judge
            # owns the deploy decision (see src/core/project_deploy_judge.py
            # + Phase 4/5 endpoints). Running the workflow's `deployment`
            # stage would burn ~10 min of devops_specialist polling for a
            # supervisor row that was marked `skipped` the instant it
            # was written. Skip it.
            #
            # Platform-level Requests (project_id is null OR proj-unassigned)
            # still go through the existing devops_specialist → supervisor
            # judge flow — that's the original feature_development path
            # and we preserve it.
            effective_skip = list(skip_stages or [])
            from src.models.base import UNASSIGNED_PROJECT_ID
            if request.project_id and request.project_id != UNASSIGNED_PROJECT_ID:
                effective_skip.append("deployment")
                logger.info(
                    "workflow_skip_deployment_for_project",
                    request_id=request_id, project_id=request.project_id,
                    reason=(
                        "AI Deploy Judge owns per-project deploys; the "
                        "workflow's deployment stage is vestigial for "
                        "project-driven Requests."
                    ),
                )

            result = await self.runner.run(
                workflow, request_id, initial_artifacts,
                skip_stages=effective_skip,
            )

            # Check if pipeline was escalated (max rework cycles exceeded)
            # Re-fetch the request from DB so we don't overwrite fields that
            # handlers (e.g., _handle_publish) may have persisted during the run.
            fresh_request = await self.state.get_request(request_id) or request

            if result.get("escalation_reason"):
                fresh_request.status = RequestStatus.FAILED
                fresh_request.completed_at = datetime.utcnow()
                # If escalation was due to a code_commit failure, persist the
                # specific CodeWriter error on the request so the UI can show
                # WHERE the workflow failed (not just "tester ran, then nothing").
                if result.get("code_commit_error"):
                    fresh_request.code_commit_error = result["code_commit_error"]
                await self.state.update_request(fresh_request)
                rework_cycles = result.get("rework_cycle", 0)
                error_msg = f"Pipeline failed after {rework_cycles} rework cycles. Quality gates did not pass."
                await self.events.emit("request.failed", {
                    "request_id": request_id,
                    "error": error_msg,
                    "escalation_reason": result["escalation_reason"][:300],
                })
                logger.warning("request_escalated_failed", request_id=request_id, cycles=rework_cycles)
            else:
                # Check if any subtasks had errors
                subtasks = await self.state.get_subtasks_for_request(request_id)
                failed_subtasks = [s for s in subtasks if s.status == "failed"]

                if failed_subtasks:
                    fresh_request.status = RequestStatus.FAILED
                    fresh_request.completed_at = datetime.utcnow()
                    await self.state.update_request(fresh_request)
                    failed_agents = [s.agent_id for s in failed_subtasks]
                    await self.events.emit("request.failed", {
                        "request_id": request_id,
                        "error": f"Agent failures: {', '.join(failed_agents)}",
                    })
                else:
                    fresh_request.status = RequestStatus.COMPLETED
                    fresh_request.completed_at = datetime.utcnow()
                    await self.state.update_request(fresh_request)
                    await self.events.emit("request.completed", {
                        "request_id": request_id, "result": str(result)[:200],
                    })
                    logger.info("request_completed", request_id=request_id)

        except Exception as e:
            logger.exception("request_failed", request_id=request_id)
            request.status = RequestStatus.FAILED
            await self.state.update_request(request)
            await self.events.emit("request.failed", {
                "request_id": request_id, "error": str(e),
            })

    async def execute_agent(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single agent task. Called by WorkflowRunner."""
        subtask_id = f"{request_id}-{agent_id.upper()}-{uuid.uuid4().hex[:4]}"
        subtask = Subtask(
            subtask_id=subtask_id,
            request_id=request_id,
            agent_id=agent_id,
        )
        await self.state.create_subtask(subtask)
        await self.events.emit("agent.started", {
            "request_id": request_id,
            "agent_id": agent_id,
            "subtask_id": subtask_id,
            "display_name": self.config.agents.get(agent_id, {}).get("display_name", agent_id),
        })

        try:
            subtask.status = SubtaskStatus.IN_PROGRESS
            subtask.started_at = datetime.utcnow()
            await self.state.update_subtask(subtask)

            # Use real agent executor if available, otherwise simulate with delay
            if self._agent_executor:
                result = await self._agent_executor.execute(
                    agent_id, request_id, inputs,
                )
            else:
                result = await self._mock_execute(agent_id, request_id, inputs)

            subtask.status = SubtaskStatus.COMPLETED
            subtask.completed_at = datetime.utcnow()
            subtask.output_artifacts = result.get("artifacts", [])
            # Save the full text output — prefer raw text, fall back to outputs dict
            subtask.output_text = result.get("text", "")
            if not subtask.output_text:
                outputs = result.get("outputs", {})
                subtask.output_text = "\n\n".join(str(v) for v in outputs.values() if v)
            await self.state.update_subtask(subtask)

            # Record token usage for cost tracking. Prefer the model reported by
            # the executor; fall back to the YAML model if absent (e.g., mock execution).
            input_tokens = result.get("input_tokens", 0)
            output_tokens = result.get("output_tokens", 0)
            if input_tokens > 0 or output_tokens > 0:
                model = result.get("model") or self.config.agents.get(agent_id, {}).get(
                    "model", "claude-opus-4-7"
                )
                await self._token_tracker.record(
                    request_id=request_id,
                    subtask_id=subtask_id,
                    agent_id=agent_id,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            # Layer 3: Parse stories and track status through pipeline
            if agent_id == "user_story_author" and subtask.output_text:
                await self._parse_and_save_stories(request_id, subtask.output_text)
            elif agent_id in ("backend_specialist", "frontend_specialist"):
                await self._update_story_statuses(request_id, agent_id, StoryStatus.IN_PROGRESS)
            elif agent_id == "code_reviewer":
                await self._update_story_statuses(request_id, agent_id, StoryStatus.REVIEW)
            elif agent_id == "tester_specialist":
                await self._update_story_statuses(request_id, agent_id, StoryStatus.TESTING)
                if subtask.output_text:
                    await self._parse_and_save_test_cases(request_id, subtask.output_text)
            elif agent_id == "devops_specialist":
                await self._update_story_statuses(request_id, agent_id, StoryStatus.DONE)

            # Save agent output as a persisted document
            if subtask.output_text and len(subtask.output_text) > 50:
                await self._save_document(request_id, agent_id, subtask.output_text)

            await self.events.emit("agent.completed", {
                "request_id": request_id,
                "agent_id": agent_id,
                "subtask_id": subtask_id,
                "display_name": self.config.agents.get(agent_id, {}).get("display_name", agent_id),
                "artifacts": subtask.output_artifacts,
            })

            # Store the output in the result dict keyed by agent role
            # so downstream agents and quality gates can find it
            result[f"{agent_id}_output"] = subtask.output_text
            if agent_id == "code_reviewer":
                result["review_report"] = subtask.output_text
            return result

        except Exception as e:
            subtask.status = SubtaskStatus.FAILED
            subtask.completed_at = datetime.utcnow()
            subtask.error_message = str(e)
            await self.state.update_subtask(subtask)
            await self.events.emit("agent.failed", {
                "request_id": request_id, "agent_id": agent_id, "error": str(e),
            })
            return {"status": "failed", "error": str(e), "outputs": {}, "artifacts": []}

    # ── Code Commit Handler ────────────────────────

    async def _handle_materialize(
        self, request_id: str, artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        """Materialise the agent's emitted code to disk RIGHT AFTER the
        development stage, BEFORE review/testing.

        This is the "Fix A" architecture: previously ``### Full Source:``
        blocks only got written at the ``code_commit`` stage, which runs
        AFTER review + testing. So the reviewer's ``file_read`` saw the
        scaffold's starter content rather than the agent's actual
        emission, and (correctly!) reported "phantom emission" — but the
        cycle couldn't recover because the agent's emission was already
        correct, just not yet on disk.

        Now: we call ``code_writer.materialize_files()`` right after the
        development stage. It writes the agent's ``### Full Source:``
        blocks to disk, runs the same lint/test gates the old commit
        path ran, and stashes the resulting {path: content} dict on
        ``artifacts`` so the later ``_handle_code_commit`` can skip the
        re-write and go straight to ``commit_to_github``.

        Failures here route to rework using the SAME shape as
        ``_handle_code_commit`` failures (``commit_status="failed"`` +
        error string) so the runner's existing rework branch picks
        them up — no new runner state machine.

        Returns dict with:
          - materialize_status: "success" | "failed" | "skipped"
          - materialized_files: dict[str, str] when success (stashed
            in artifacts by the runner so _handle_code_commit can
            short-circuit)
          - commit_status: mirrors materialize_status for the runner's
            existing rework-on-code_commit-failure branch (so we don't
            need a new runner code path)
          - error: failure message when status=failed
        """
        from src.core.code_writer import CodeWriteError

        # Re-use the same agent_outputs collection logic as code_commit
        # so we materialise EXACTLY what code_commit would have written.
        agent_outputs = await self._collect_agent_outputs(request_id)
        if not agent_outputs:
            logger.info(
                "materialize_skipped_no_outputs",
                request_id=request_id,
                reason=(
                    "No backend/frontend specialist outputs yet. Common for "
                    "workflows whose first stage isn't development "
                    "(documentation_update, research) — they reach this "
                    "hook with no code to write."
                ),
            )
            return {"materialize_status": "skipped"}

        description = artifacts.get("description", request_id)[:80]
        project_root = await self._resolve_project_root_for_request(request_id)
        try:
            files = await self._code_writer.materialize_files(
                request_id=request_id,
                description=description,
                agent_outputs=agent_outputs,
                project_root=project_root,
            )
        except CodeWriteError as e:
            # Same shape as _handle_code_commit failure so the runner's
            # existing rework branch handles it without modification.
            enriched_error = _enrich_error_with_line_snippets(
                str(e), project_root,
            )
            await self.events.emit("materialize.failed", {
                "request_id": request_id, "error": str(e),
            })
            logger.warning(
                "materialize_failed",
                request_id=request_id, error=str(e),
            )
            return {
                "materialize_status": "failed",
                # Mirror as commit_status so the runner's existing
                # rework-on-code_commit-failure branch picks this up.
                "commit_status": "failed",
                "error": enriched_error,
            }
        await self.events.emit("materialize.completed", {
            "request_id": request_id, "files": list(files.keys()),
        })
        logger.info(
            "materialize_completed",
            request_id=request_id, file_count=len(files),
        )
        return {
            "materialize_status": "success",
            "materialized_files": files,
        }

    async def _resolve_project_root_for_request(self, request_id: str):
        """Look up request → project → project_root_dir. Returns None
        for platform-level Requests so callers fall back to the
        platform tree. Shared by the materialize handler and the
        code_commit handler — both need the same per-project routing."""
        from src.core.project_workspace import project_root_dir
        from src.models.base import UNASSIGNED_PROJECT_ID
        try:
            req = await self.state.get_request(request_id)
            if not req or not req.project_id:
                return None
            if req.project_id == UNASSIGNED_PROJECT_ID:
                return None
            proj = await self.state.get_project(req.project_id)
            if not proj:
                return None
            return project_root_dir(proj.name)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "orchestrator_project_root_resolve_failed",
                request_id=request_id, error=str(e),
            )
            return None

    async def _collect_agent_outputs(self, request_id: str) -> dict[str, str]:
        """Collect backend/frontend specialist outputs across all subtask
        cycles. Same logic used by both _handle_materialize and
        _handle_code_commit so they materialise/commit EXACTLY the same
        agent output set."""
        agent_outputs: dict[str, str] = {}
        subtasks = await self.state.get_subtasks_for_request(request_id)
        code_agents = ("backend_specialist", "frontend_specialist")
        for idx, st in enumerate(
            sorted(
                (s for s in subtasks if s.agent_id in code_agents and s.output_text),
                key=lambda s: s.started_at,
            )
        ):
            agent_outputs[f"{st.agent_id}_cycle_{idx:02d}"] = st.output_text
        return agent_outputs

    async def _handle_code_commit(self, request_id: str, artifacts: dict[str, Any]) -> dict[str, Any]:
        """Parse agent code outputs, write to disk, compile, test, git push."""
        from src.core.code_writer import CodeWriteError

        await self.events.emit("code_commit.started", {"request_id": request_id})
        logger.info("code_commit_started", request_id=request_id)

        # Collect code outputs from backend and frontend specialists.
        # IMPORTANT: we collect from EVERY subtask cycle, not just the latest
        # one in artifacts. Reason: when a rework cycle happens, only that
        # cycle's output ends up in `artifacts` — but the cycle-0 agent's
        # work (e.g., search_replace edits on disk, `## Files Modified`
        # listings) lives only in the cycle-0 subtask row. CodeWriter needs
        # to see both so its `### Full Source:` parser AND its `## Files
        # Modified` parser can pick up everything that was ever emitted.
        # Without this, the rework-cycle agent's empty/incomplete Files
        # Modified section silently drops earlier edits from the commit
        # (the REQ-8C3B4F failure: cycle-1 listed only its own self-yaml-
        # edit, so the real prompts.py + PromptStudio.tsx changes from
        # cycle 0 were ON DISK but never made it to GitHub).
        agent_outputs: dict[str, str] = {}
        subtasks = await self.state.get_subtasks_for_request(request_id)
        code_agents = ("backend_specialist", "frontend_specialist")
        for idx, st in enumerate(
            sorted(
                (s for s in subtasks if s.agent_id in code_agents and s.output_text),
                key=lambda s: s.started_at,
            )
        ):
            # Key by agent_id + cycle index so each cycle's output is a
            # separate entry in the dict. CodeWriter iterates over the dict
            # and parses each value independently, so all `### Full Source:`
            # blocks AND all `## Files Modified` lists get picked up.
            # Later cycles overwrite earlier ones for the SAME file content
            # via dict update (correct: latest emitted version wins), but
            # files only emitted in cycle 0 still survive.
            agent_outputs[f"{st.agent_id}_cycle_{idx:02d}"] = st.output_text

        # Backward-compatible fallback to artifacts in case the subtask
        # query missed something (no row yet for the current cycle's agent).
        for key, value in artifacts.items():
            if not isinstance(value, str):
                continue
            for agent in code_agents:
                if (agent in key or agent.split("_")[0] + "_code" in key) and value:
                    fallback_key = f"{agent}_fallback"
                    if fallback_key not in agent_outputs:
                        agent_outputs[fallback_key] = value

        if not agent_outputs:
            logger.warning("no_code_outputs_for_commit", request_id=request_id)
            return {"commit_status": "skipped", "reason": "No code outputs found"}

        logger.info(
            "code_commit_collecting_outputs",
            request_id=request_id,
            outputs_count=len(agent_outputs),
            keys=list(agent_outputs.keys()),
        )

        try:
            description = artifacts.get("description", request_id)[:80]

            # WS-09 — resolve which repo this request's code should land in.
            # Look up request → project → repo_url. If the project has a
            # repo_url that parses as a GitHub URL, target it. Otherwise
            # fall back to GITHUB_REPO env var (the platform's own repo).
            #
            # Per-project working tree feature — same lookup also yields
            # the project name, which becomes the on-disk root for this
            # commit's file writes. Without a project_id (legacy
            # one-off requests), project_root stays None and the writer
            # falls back to self.root (= the platform tree at /app/).
            from pathlib import Path as _Path

            from src.core.github_publisher import extract_owner_repo
            from src.core.project_workspace import project_root_dir
            import os as _os
            target_repo: str | None = None
            project_root: _Path | None = None
            req_for_repo = await self.state.get_request(request_id)
            if req_for_repo and req_for_repo.project_id:
                proj = await self.state.get_project(req_for_repo.project_id)
                if proj:
                    if proj.repo_url:
                        parsed = extract_owner_repo(proj.repo_url)
                        if parsed:
                            target_repo = f"{parsed[0]}/{parsed[1]}"
                    try:
                        project_root = project_root_dir(proj.name)
                    except ValueError:
                        # validate_name rejected the name — shouldn't
                        # happen because create_project ran the same
                        # validator. Log and fall back to platform tree
                        # so the request doesn't wedge.
                        logger.warning(
                            "code_commit.project_root_resolve_failed",
                            project_id=proj.project_id, name=proj.name,
                        )
                        project_root = None
            if not target_repo:
                target_repo = _os.getenv("GITHUB_REPO") or None  # CodeWriter falls back too

            # If the workflow runner already called _handle_materialize
            # (Fix A path), it stashed the materialised files dict on
            # ``artifacts``. Pass that to commit_code so it skips the
            # parse/write/lint/test work — those are done; we're just
            # committing to GitHub now.
            materialized_files: dict[str, str] | None = (
                artifacts.get("materialized_files") if isinstance(
                    artifacts.get("materialized_files"), dict,
                ) else None
            )
            dep_state = await self._code_writer.commit_code(
                request_id, description, agent_outputs,
                repo=target_repo,
                project_root=project_root,
                materialized_files=materialized_files,
            )

            # Persist artifact metadata on the Request so the UI can display it after page reload
            try:
                req = await self.state.get_request(request_id)
                if req:
                    req.published_files = dep_state.files_committed or []
                    req.commit_sha = dep_state.commit_sha
                    # Build a GitHub commit URL from the ACTUAL target repo (not just env)
                    if target_repo and dep_state.commit_sha:
                        req.commit_url = f"https://github.com/{target_repo}/commit/{dep_state.commit_sha}"
                    await self.state.update_request(req)
            except Exception as e:
                logger.warning("code_commit_persist_failed", request_id=request_id, error=str(e))

            await self.events.emit("code_commit.completed", {
                "request_id": request_id,
                "commit_sha": dep_state.commit_sha,
                "files": dep_state.files_committed,
            })

            return {
                "commit_sha": dep_state.commit_sha,
                "files_committed": dep_state.files_committed,
                "deployment_id": dep_state.deployment_id,
                "commit_status": "success",
            }

        except CodeWriteError as e:
            await self.events.emit("code_commit.failed", {
                "request_id": request_id, "error": str(e),
            })
            logger.error("code_commit_failed", request_id=request_id, error=str(e))
            # Enrich the error with the CURRENT content of every cited line
            # before we hand it back. Previously the rework prompt said
            # "errors.py:89:89 line too long" and the agent had to call
            # file_read just to see what was there — now the line content
            # (with 2 lines of context) is embedded inline so the agent
            # can fix in the very next turn without an extra round-trip.
            enriched_error = _enrich_error_with_line_snippets(str(e), project_root)
            # Return a failure result instead of raising. The runner inspects
            # commit_status=="failed" and decides whether to rework (if budget
            # remains) or escalate. This is what makes code_commit failures
            # recoverable instead of fatal — the rework cycle now covers
            # truncation/ruff/tsc errors that previously killed the request.
            return {
                "commit_status": "failed",
                "error": enriched_error,
            }

    async def _handle_publish(self, request_id: str, artifacts: dict[str, Any]) -> dict[str, Any]:
        """Publish research artifacts to docs/research/ and GitHub. Soft-fails on errors."""
        await self.events.emit("research_publish.started", {"request_id": request_id})
        logger.info("research_publish_started", request_id=request_id)

        description = artifacts.get("description", request_id)

        # WS-12/14 — resolve project_slug + project_repo from the request's
        # project so research artifacts land in the project's workspace and
        # its own GitHub repo (not the platform's). Falls through to legacy
        # behavior (platform repo, flat path) when the project has no
        # repo_url or no project at all.
        from src.core.github_publisher import extract_owner_repo
        from src.core.project_validation import project_repo_slug as _slug_for
        project_slug: str | None = None
        project_repo: str | None = None
        req_for_proj = await self.state.get_request(request_id)
        if req_for_proj and req_for_proj.project_id:
            proj = await self.state.get_project(req_for_proj.project_id)
            if proj:
                # Use the same slug the repo was created with so the local
                # workspace name matches the GitHub repo name.
                try:
                    project_slug = _slug_for(proj.name)
                except ValueError:
                    project_slug = None
                if proj.repo_url:
                    parsed = extract_owner_repo(proj.repo_url)
                    if parsed:
                        project_repo = f"{parsed[0]}/{parsed[1]}"

        result = await self._research_publisher.publish(
            request_id, description, artifacts,
            project_slug=project_slug,
            project_repo=project_repo,
        )

        # Persist artifact metadata on the Request so the UI can display it after page reload
        try:
            req = await self.state.get_request(request_id)
            if req:
                req.published_files = result.get("published_files", []) or []
                req.commit_sha = result.get("commit_sha")
                req.commit_url = result.get("commit_url")
                await self.state.update_request(req)
        except Exception as e:
            logger.warning("publish_persist_failed", request_id=request_id, error=str(e))

        if result.get("publish_error"):
            await self.events.emit("research_publish.partial", {
                "request_id": request_id,
                "error": result["publish_error"],
                "files": result.get("published_files", []),
            })
            logger.warning(
                "research_publish_partial",
                request_id=request_id,
                error=result["publish_error"],
            )
        else:
            await self.events.emit("research_publish.completed", {
                "request_id": request_id,
                "commit_sha": result.get("commit_sha"),
                "commit_url": result.get("commit_url"),
                "files": result.get("published_files", []),
            })
            logger.info(
                "research_published",
                request_id=request_id,
                commit_sha=result.get("commit_sha"),
                files=len(result.get("published_files", [])),
            )

        return result

    # ── Document Persistence ───────────────────────

    AGENT_DOC_TYPE_MAP = {
        "prd_specialist": "prd",
        "user_story_author": "user_stories",
        "backend_specialist": "backend_code",
        "frontend_specialist": "frontend_code",
        "code_reviewer": "code_review",
        "tester_specialist": "test_report",
        "devops_specialist": "deploy_report",
        "research_specialist": "research_report",
        "content_creator": "content_artifact",
    }

    async def _save_document(self, request_id: str, agent_id: str, content: str) -> None:
        """Save agent output as a persisted document in the knowledge base."""
        doc_type = self.AGENT_DOC_TYPE_MAP.get(agent_id)
        if not doc_type:
            return
        try:
            # Extract title from first heading or first line
            title = request_id
            for line in content.split("\n"):
                line = line.strip().lstrip("#").strip()
                if line and len(line) > 5:
                    title = line[:150]
                    break

            tags = self._extract_keywords(content[:1000])

            doc = Document(
                document_id=f"doc-{uuid.uuid4().hex[:8]}",
                request_id=request_id,
                doc_type=doc_type,
                title=title,
                content=content,
                agent_id=agent_id,
                tags=tags,
            )
            await self.state.save_document(doc)
            logger.info("document_saved", doc_type=doc_type, request_id=request_id, title=title[:50])
        except Exception as e:
            logger.warning("document_save_failed", error=str(e), agent_id=agent_id)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract searchable keywords from text content."""
        # Remove markdown formatting
        clean = re.sub(r'[#*`\[\]()_\-|]', ' ', text.lower())
        # Split into words
        words = clean.split()
        # Filter: length > 3, not common stopwords
        stopwords = {
            'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was', 'were',
            'been', 'have', 'has', 'will', 'would', 'could', 'should', 'must', 'shall',
            'not', 'but', 'all', 'each', 'every', 'some', 'any', 'when', 'where', 'what',
            'which', 'who', 'how', 'than', 'then', 'into', 'also', 'only', 'more', 'most',
            'just', 'about', 'above', 'below', 'between', 'does', 'done', 'such', 'they',
            'them', 'their', 'there', 'these', 'those', 'being', 'both', 'same', 'other',
        }
        keywords = list(dict.fromkeys(  # preserve order, deduplicate
            w for w in words if len(w) > 3 and w not in stopwords and w.isalpha()
        ))
        return keywords[:20]  # Cap at 20 tags

    @staticmethod
    def _namespace_story_id(request_id: str, raw_id: str) -> str:
        """Convert an agent-emitted story ID into a globally unique one.

        The agent emits per-document IDs like ``US-001`` (no request context),
        but ``stories.story_id`` is a PRIMARY KEY in SQLite — two requests both
        emitting ``US-001`` collide and the second one silently fails the whole
        parse with a UNIQUE constraint violation.

        We prefix the request portion in the same shape the demo seed already
        uses (``US-038-001``), so the stored ID reads naturally:
            request REQ-98E4C2 + agent's US-001 → US-98E4C2-001

        Idempotent: if the agent already emitted a namespaced ID (anything with
        an inner dash like US-038-001), we keep it as-is.
        """
        suffix = raw_id.removeprefix("US-")
        if "-" in suffix:
            return raw_id  # already namespaced
        req_part = request_id.removeprefix("REQ-")
        return f"US-{req_part}-{suffix}"

    async def _parse_and_save_stories(self, request_id: str, output_text: str) -> None:
        """Parse user stories from the User Story Author's output and save to DB.

        Resilient to per-story failures: if one story can't be inserted (e.g.,
        primary-key collision because of a buggy ID, or any other DB error),
        the rest of the batch is still attempted. Without this, a single bad
        story would drop the entire request's story list.
        """
        try:
            stories = self._extract_stories(request_id, output_text)
            # Split output into per-story blocks for AC extraction. Key the map
            # by the SAME namespaced ID we assigned to the Story object above,
            # so block lookup matches story.story_id exactly.
            story_blocks = re.split(r'(?=###\s+(?:Story:?\s*)?(?:US-|\[US-))', output_text)
            block_map: dict[str, str] = {}
            for block in story_blocks:
                id_match = re.search(r'(US-\d+(?:-\d+)?)', block)
                if id_match:
                    namespaced = self._namespace_story_id(request_id, id_match.group(1))
                    block_map[namespaced] = block

            saved = 0
            insert_failures = 0
            for story in stories:
                try:
                    await self.state.create_story(story)
                except Exception as e:
                    insert_failures += 1
                    logger.warning(
                        "story_insert_failed",
                        request_id=request_id, story_id=story.story_id, error=str(e),
                    )
                    continue

                # Save acceptance criteria for this story. Per-AC try/except so
                # one bad AC doesn't stop the others.
                block_text = block_map.get(story.story_id, story.description)
                acs = self._extract_acceptance_criteria(story.story_id, block_text)
                for ac in acs:
                    try:
                        await self.state.create_acceptance_criterion(ac)
                    except Exception as e:
                        logger.warning(
                            "ac_insert_failed",
                            request_id=request_id, ac_id=ac.ac_id, error=str(e),
                        )
                await self.events.emit("story.created", {
                    "request_id": request_id,
                    "story_id": story.story_id,
                    "title": story.title,
                    "status": story.status,
                    "acceptance_criteria_count": len(acs),
                })
                saved += 1

            logger.info(
                "stories_parsed",
                request_id=request_id,
                count=saved,
                insert_failures=insert_failures,
            )
        except Exception as e:
            logger.warning("story_parsing_failed", request_id=request_id, error=str(e))

    def _extract_stories(self, request_id: str, text: str) -> list[Story]:
        """Extract individual stories from User Story Author output.

        All returned `story_id` values are namespaced by request_id (see
        `_namespace_story_id`) to guarantee uniqueness across multiple requests
        that might both emit ``US-001``.
        """
        stories: list[Story] = []
        # Pattern 1: ### US-XXX or ### Story: US-XXX
        story_blocks = re.split(r'(?=###\s+(?:Story:?\s*)?(?:US-|\[US-))', text)
        counter = 1

        for block in story_blocks:
            block = block.strip()
            if not block:
                continue

            # Extract story ID from the block, then namespace it.
            id_match = re.search(r'(US-\d+(?:-\d+)?)', block)
            if id_match:
                story_id = self._namespace_story_id(request_id, id_match.group(1))
            else:
                story_id = f"US-{request_id[-6:]}-{counter:03d}"

            # Extract title — first line after ###
            title_match = re.search(r'###\s+(?:Story:?\s*)?(?:\[?US-\d+(?:-\d+)?\]?\s*)?(.+)', block)
            title = title_match.group(1).strip().strip("*").strip() if title_match else f"Story {counter}"
            if not title or title == story_id:
                # Try next non-empty line
                lines = [l.strip() for l in block.split("\n") if l.strip() and not l.strip().startswith("#")]
                title = lines[0][:100] if lines else f"Story {counter}"

            # Extract priority
            priority = None
            pri_match = re.search(r'[Pp]riority:?\s*(Critical|High|Medium|Low)', block, re.IGNORECASE)
            if pri_match:
                priority = pri_match.group(1).lower()

            # Get the full story text as description
            description = block[:2000]

            stories.append(Story(
                story_id=story_id,
                request_id=request_id,
                title=title[:200],
                description=description,
                status=StoryStatus.TODO,
                priority=priority,
            ))
            counter += 1

        # If no ### patterns found, try simpler formats (bullet lists, numbered lists)
        if not stories:
            lines = text.strip().split("\n")
            for line in lines:
                line = line.strip()
                # Match: - US-001: Title or 1. Title or - **Title**
                match = re.match(r'(?:[-*]\s+|(?:\d+\.)\s+)(?:(US-\d+(?:-\d+)?)[:\s]+)?(.+)', line)
                if match and len(match.group(2).strip()) > 5:
                    if match.group(1):
                        sid = self._namespace_story_id(request_id, match.group(1))
                    else:
                        sid = f"US-{request_id[-6:]}-{counter:03d}"
                    title = match.group(2).strip().strip("*").strip()
                    stories.append(Story(
                        story_id=sid,
                        request_id=request_id,
                        title=title[:200],
                        description=line,
                        status=StoryStatus.TODO,
                    ))
                    counter += 1

        return stories

    def _extract_acceptance_criteria(
        self, story_id: str, block_text: str
    ) -> list[AcceptanceCriterion]:
        """Extract Given/When/Then acceptance criteria from a story text block."""
        criteria: list[AcceptanceCriterion] = []

        # Find the "Acceptance Criteria:" section
        ac_match = re.search(
            r'\*{0,2}Acceptance\s+Criteria:?\*{0,2}\s*\n(.*?)(?:\n\*{0,2}(?:Notes|Priority|Effort|Traces|---)|$)',
            block_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not ac_match:
            return criteria

        ac_section = ac_match.group(1)

        # Split into individual criteria (bullet points starting with - or *)
        bullets = re.findall(r'[-*]\s+(.+?)(?=\n[-*]\s+|\Z)', ac_section, re.DOTALL)

        for idx, bullet in enumerate(bullets, start=1):
            text = bullet.strip().replace("\n", " ").strip()
            if not text:
                continue

            # Parse Given / When / Then clauses
            given, when, then = "", "", ""
            gwt = re.match(
                r'[Gg]iven\s+(.+?),?\s+[Ww]hen\s+(.+?),?\s+[Tt]hen\s+(.+)',
                text,
            )
            if gwt:
                given = gwt.group(1).strip().rstrip(",")
                when = gwt.group(2).strip().rstrip(",")
                then = gwt.group(3).strip().rstrip(".")
            else:
                # Try alternate format: just "When X, Then Y" (no Given)
                wt = re.match(r'[Ww]hen\s+(.+?),?\s+[Tt]hen\s+(.+)', text)
                if wt:
                    when = wt.group(1).strip().rstrip(",")
                    then = wt.group(2).strip().rstrip(".")

            ac_id = f"{story_id}-AC-{idx:02d}"
            criteria.append(AcceptanceCriterion(
                ac_id=ac_id,
                story_id=story_id,
                criterion_text=text,
                given_clause=given,
                when_clause=when,
                then_clause=then,
            ))

        return criteria

    async def _parse_and_save_test_cases(
        self, request_id: str, output_text: str
    ) -> None:
        """Parse test cases from Tester output, link to stories, and extract coverage.

        Per-row exception isolation: a single test_case that fails to
        persist (legacy plain-INSERT raised UNIQUE on rework cycle 2;
        with the new UPSERT this is much rarer but still possible if a
        constraint we haven't anticipated trips) MUST NOT abort the
        whole batch. T-b4954195 / T-3e1303b3 both died because one
        failing row took the entire story_tc_stats dict down with it,
        which then flipped the combined gate to test_passed=False even
        though the rest of the tests were fine.
        """
        try:
            stories = await self.state.get_stories_for_request(request_id)
            story_map = {s.story_id: s for s in stories}

            test_cases = self._extract_test_cases(output_text, story_map)

            # Group TCs by story for coverage calculation
            story_tc_stats: dict[str, dict[str, int]] = {}  # story_id -> {total, passed}
            row_failures = 0
            for tc in test_cases:
                # Per-row try/except: a single bad row doesn't bail the
                # batch. The combined-gate consumer reads story_tc_stats
                # to decide test_passed, so losing one row is much less
                # bad than losing all of them.
                try:
                    await self.state.create_test_case(tc)
                except Exception as e:  # noqa: BLE001
                    row_failures += 1
                    logger.warning(
                        "test_case_row_persist_failed",
                        request_id=request_id,
                        test_id=tc.test_id, error=str(e)[:120],
                    )
                    continue
                stats = story_tc_stats.setdefault(tc.story_id, {"total": 0, "passed": 0})
                stats["total"] += 1
                if tc.status == "pass":
                    stats["passed"] += 1

            # SBD-03: Update coverage_pct per story based on pass rate
            for story_id, stats in story_tc_stats.items():
                if story_id in story_map and stats["total"] > 0:
                    story = story_map[story_id]
                    story.coverage_pct = round(stats["passed"] / stats["total"] * 100, 1)
                    await self.state.update_story(story)

            logger.info(
                "test_cases_parsed",
                request_id=request_id,
                count=len(test_cases),
                stories_with_coverage=len(story_tc_stats),
                row_failures=row_failures,
            )
        except Exception as e:
            logger.warning("test_case_parsing_failed", request_id=request_id, error=str(e))

    def _extract_test_cases(
        self, text: str, story_map: dict[str, Story]
    ) -> list[TestCase]:
        """Extract test cases from Tester markdown table output."""
        test_cases: list[TestCase] = []

        # Match table rows: | TC-XXX | name | US-XXX AC-X | type | status | notes |
        row_pattern = re.compile(
            r'\|\s*(TC-\d+)\s*\|\s*(.+?)\s*\|\s*(US-\S+)\s*(AC-\d+)?\s*\|\s*\w+\s*\|\s*'
            r'(.+?)\s*\|\s*.*?\|'
        )
        for match in row_pattern.finditer(text):
            tc_id = match.group(1)
            tc_name = match.group(2).strip()
            story_ref = match.group(3).strip()
            status_raw = match.group(5).strip()

            # Normalize status
            status = "pending"
            if "PASS" in status_raw.upper() or "\u2705" in status_raw:
                status = "pass"
            elif "FAIL" in status_raw.upper() or "\u274c" in status_raw:
                status = "fail"
            elif "SKIP" in status_raw.upper() or "\u26a0" in status_raw:
                status = "pending"

            # Resolve story_id — try exact match, then with request prefix
            story_id = story_ref
            if story_id not in story_map:
                # Try matching by suffix (e.g., "US-001" might map to "US-001-001")
                for sid in story_map:
                    if sid.startswith(story_ref) or sid.endswith(story_ref.replace("US-", "")):
                        story_id = sid
                        break

            # Only create TC if we have a valid story to link to
            if story_id in story_map:
                test_cases.append(TestCase(
                    test_id=tc_id,
                    story_id=story_id,
                    name=tc_name,
                    status=status,
                    last_run_at=datetime.utcnow() if status in ("pass", "fail") else None,
                ))

        # Fallback: try simpler line-based format if no table rows found
        if not test_cases:
            line_pattern = re.compile(
                r'(TC-\d+)\s*[:\|]\s*(.+?)\s*[:\|]\s*(US-\S+).*?(PASS|FAIL|SKIP)',
                re.IGNORECASE,
            )
            for match in line_pattern.finditer(text):
                tc_id = match.group(1)
                tc_name = match.group(2).strip()
                story_ref = match.group(3).strip()
                status = match.group(4).strip().lower()
                if status == "skip":
                    status = "pending"

                story_id = story_ref
                if story_id not in story_map:
                    for sid in story_map:
                        if sid.startswith(story_ref):
                            story_id = sid
                            break

                if story_id in story_map:
                    test_cases.append(TestCase(
                        test_id=tc_id,
                        story_id=story_id,
                        name=tc_name,
                        status=status,
                        last_run_at=datetime.utcnow() if status in ("pass", "fail") else None,
                    ))

        return test_cases

    async def _update_story_statuses(
        self, request_id: str, agent_id: str, new_status: StoryStatus
    ) -> None:
        """Update story statuses based on which agent completed work."""
        stories = await self.state.get_stories_for_request(request_id)
        for story in stories:
            if story.status != StoryStatus.DONE:
                story.status = new_status
                if not story.assigned_agent:
                    story.assigned_agent = agent_id
                await self.state.update_story(story)

    async def _mock_execute(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Simulate agent execution with realistic delays and mock outputs."""
        delay_range = MOCK_AGENT_DELAYS.get(agent_id, (2.0, 5.0))
        delay = random.uniform(*delay_range)

        logger.info("mock_agent_working", agent_id=agent_id, delay=f"{delay:.1f}s")

        # Emit progress events during the simulated work
        steps = max(2, int(delay / 1.5))
        for i in range(steps):
            await asyncio.sleep(delay / steps)
            await self.events.emit("agent.progress", {
                "request_id": request_id,
                "agent_id": agent_id,
                "progress": round((i + 1) / steps * 100),
                "display_name": self.config.agents.get(agent_id, {}).get("display_name", agent_id),
                "message": _mock_progress_message(agent_id, i, steps),
            })

        # Generate mock output based on agent role
        mock_output = _mock_agent_output(agent_id, request_id, inputs)
        return {
            "status": "completed",
            "outputs": mock_output,
            "artifacts": mock_output.get("artifacts", []),
        }


def _mock_progress_message(agent_id: str, step: int, total: int) -> str:
    """Generate a realistic progress message for mock execution."""
    messages: dict[str, list[str]] = {
        "engineering_lead": ["Analyzing request...", "Decomposing into subtasks...", "Creating delegation plan..."],
        "prd_specialist": ["Gathering requirements...", "Writing PRD sections...", "Defining acceptance criteria...", "Finalizing document..."],
        "user_story_author": ["Creating user stories...", "Writing acceptance criteria...", "Assigning priorities..."],
        "code_reviewer": ["Setting up feature branch...", "Reviewing code structure...", "Checking code quality...", "Posting review comments..."],
        "backend_specialist": ["Implementing API endpoints...", "Writing database models...", "Creating unit tests...", "Running test suite..."],
        "frontend_specialist": ["Building React components...", "Styling with Tailwind...", "Adding interactions...", "Writing component tests..."],
        "devops_specialist": ["Configuring deployment...", "Running smoke tests...", "Verifying health checks..."],
        "tester_specialist": ["Writing E2E tests...", "Executing test suite...", "Generating coverage report..."],
    }
    agent_messages = messages.get(agent_id, ["Processing..."])
    idx = min(step, len(agent_messages) - 1)
    return agent_messages[idx]


def _mock_agent_output(agent_id: str, request_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Generate realistic mock output for each agent type."""
    desc = inputs.get("description", "")[:80]
    outputs: dict[str, dict[str, Any]] = {
        "engineering_lead": {
            "delegation_plan": f"Delegated {request_id}: planning → development → delivery",
        },
        "prd_specialist": {
            "prd_document": f"# PRD: {desc}\n\n## Requirements\n- REQ-001: Core functionality\n- REQ-002: User interface\n- REQ-003: Testing",
            "artifacts": [f"docs/prd/{request_id}.md"],
        },
        "user_story_author": {
            "user_stories": f"## Stories for {request_id}\n- US-001: Implement core feature\n- US-002: Build UI components\n- US-003: Write tests",
            "artifacts": [f"docs/stories/{request_id}.md"],
        },
        "code_reviewer": {
            "review_report": f"Code review for {request_id}: All checks passed. Coverage: 85%",
        },
        "backend_specialist": {
            "backend_code": f"Implemented API endpoints for {request_id}",
            "artifacts": [f"src/api/{request_id.lower()}.py", f"tests/test_{request_id.lower()}.py"],
        },
        "frontend_specialist": {
            "frontend_code": f"Built React components for {request_id}",
            "artifacts": [f"frontend/src/pages/{request_id}.tsx"],
        },
        "devops_specialist": {
            "deployment_report": f"Deployed {request_id} to staging → production. Health checks passed.",
        },
        "tester_specialist": {
            "test_report": (
                f"## Test Results\n\n"
                f"| # | Test Case | Traces To | Type | Status | Notes |\n"
                f"|---|-----------|-----------|------|--------|-------|\n"
                f"| TC-001 | Core feature validation | US-001 AC-1 | Unit | ✅ PASS | — |\n"
                f"| TC-002 | Input validation checks | US-001 AC-2 | Unit | ✅ PASS | — |\n"
                f"| TC-003 | Error handling paths | US-001 AC-3 | Unit | ✅ PASS | — |\n"
                f"| TC-004 | UI component rendering | US-002 AC-1 | Integration | ✅ PASS | — |\n"
                f"| TC-005 | Form submission flow | US-002 AC-2 | Integration | ✅ PASS | — |\n"
                f"| TC-006 | Negative test scenario | US-002 AC-3 | E2E | ❌ FAIL | Edge case timeout |\n"
                f"| TC-007 | Test suite coverage | US-003 AC-1 | Unit | ✅ PASS | — |\n\n"
                f"## Summary\n\n"
                f"| Metric | Value |\n"
                f"|--------|-------|\n"
                f"| Total | 7 |\n"
                f"| Passed | 6 ✅ |\n"
                f"| Failed | 1 ❌ |\n"
                f"| Pass Rate | 86% |\n\n"
                f"## Verdict\n**NEEDS FIXES** — 1 test failing on edge case timeout in form submission"
            ),
            "artifacts": [f"tests/e2e/{request_id.lower()}_test.py"],
        },
    }
    return outputs.get(agent_id, {"output": f"Completed by {agent_id}"})
