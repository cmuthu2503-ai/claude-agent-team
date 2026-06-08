"""Self-learning event handler — AET-11.

Replaces the orchestrator's inline `_trigger_self_learning` calls with
a proper EventEmitter handler registered at app boot. The handler
listens for ``request.failed`` events, builds a structured failure
context from the database + subtask trail, and fires the
``self_learning_agent`` via ``single_agent_call()`` so the analysis
runs silently — no Request row, no Subtask row, no UI noise on the
main request timeline (the agent's job is to write a lesson, not to
appear in the failure's audit log).

Why a handler instead of an inline call:
  - Other components (UI badges, analytics, alerting) can subscribe to
    the same events. Inline calls don't scale; events do.
  - The orchestrator stays focused on workflow execution. Lesson
    learning is a side effect, not a workflow stage.
  - Testable in isolation — feed the handler a synthetic event,
    assert on what it calls.

Failure-context fields (per AET-11 spec):
  - request_id      — from the event payload
  - final_error     — from the event payload's `error` field
  - retries         — rework cycles, queried from state
  - agent_traces    — last 1000 chars of each failed subtask's output
  - related_files   — files the agents touched (parsed from subtask outputs)

The handler is wired in src/main.py alongside the existing
auto_dispatch_handler_registered. Idempotent on its own event-filter
side: a `request.failed` for a request_id whose self-learning task is
already in flight is dropped (the inflight set is a process-local guard
to prevent double-fire when the orchestrator re-emits — old behavior
was to call _trigger_self_learning from 3 different code paths, each
of which could fire on a single failure).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog

from src.core.events import EventEmitter
from src.state.base import StateStore

logger = structlog.get_logger()


# Per-process inflight guard — prevents double-fire when the orchestrator
# emits request.failed from multiple code paths for the same request
# (escalation path + agent-failure path can both fire). The agent run
# is ~30-60s; the set drains itself when the agent completes or errors.
_INFLIGHT_REQUESTS: set[str] = set()

# Cap on per-subtask trace length. The agent system prompt is already
# loaded with the lessons file (~50 KB cap); piling on full subtask
# transcripts pushes us toward the model context budget. 1000 chars
# per subtask × 5-10 subtasks = 5-10 KB of context for the agent.
_TRACE_CHAR_CAP = 1000

# How many of the most recent failed subtasks to include. More than
# this and the prompt gets noisy.
_MAX_TRACES = 5


# ── File-path extractor ──────────────────────────────────────────────────


# Matches the materializer's `### \`path\`` emissions (most common form)
# and bare `path/to/file.ext` references in the output text. Restricts
# to known source extensions so unrelated mentions (e.g. "/tmp/foo")
# don't bloat the related_files set.
_FILE_PATH_RE = re.compile(
    r"(?:###\s+(?:Full Source:\s*)?`([^`]+)`)"
    r"|(?:\b((?:src|frontend|tests|supervisor|config|docs)/[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|yaml|yml|md|json|sql)))",
)


def _extract_related_files(subtask_outputs: list[str]) -> list[str]:
    """Walk the failed subtask outputs and pull out every file path
    they emitted or referenced. Deduped while preserving first-seen
    order so the agent's context starts with the most-touched files."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for text in subtask_outputs:
        if not text:
            continue
        for m in _FILE_PATH_RE.finditer(text):
            path = m.group(1) or m.group(2)
            if not path:
                continue
            path = path.strip()
            # Guard against path traversal in extracted paths.
            if ".." in path or path.startswith("/"):
                continue
            if path not in seen_set:
                seen.append(path)
                seen_set.add(path)
    return seen[:20]  # cap so the prompt doesn't explode on a noisy failure


# ── Failure-context builder ──────────────────────────────────────────────


async def _build_failure_context(
    state: StateStore,
    request_id: str,
    final_error: str,
) -> dict[str, Any]:
    """Assemble the failure_context dict the AET-11 spec calls for.

    Always returns a dict — falls back to empty fields on lookup errors
    rather than raising. The agent can handle missing fields; an
    exception here would prevent any lesson from being captured.
    """
    retries = 0
    agent_traces: list[str] = []
    failed_subtasks: list[Any] = []

    try:
        request = await state.get_request(request_id)
        # `rework_cycles` doesn't live on the Request directly — it's
        # captured implicitly in the count of repeated subtasks per
        # agent_id. Approximate: total subtasks / unique agent_ids.
        subtasks = await state.get_subtasks_for_request(request_id)
        if subtasks:
            unique_agents = {s.agent_id for s in subtasks}
            retries = max(0, len(subtasks) // max(1, len(unique_agents)) - 1)
            failed_subtasks = [s for s in subtasks if s.status == "failed"]
            # Pick the last N failed subtasks' outputs, capped per the
            # AET-11 spec (1000 chars each).
            for s in failed_subtasks[-_MAX_TRACES:]:
                trace_text = ((s.output_text or "")[:_TRACE_CHAR_CAP]).strip()
                if trace_text:
                    agent_traces.append(
                        f"### {s.agent_id} (subtask {s.subtask_id})\n{trace_text}"
                    )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "self_learning_context_lookup_failed",
            request_id=request_id, error=str(e),
        )

    related_files = _extract_related_files(
        [s.output_text or "" for s in failed_subtasks]
    )

    return {
        "request_id": request_id,
        "final_error": final_error or "(no error message in event payload)",
        "retries": retries,
        "agent_traces": agent_traces,
        "related_files": related_files,
    }


def _format_prompt(failure_context: dict[str, Any]) -> str:
    """Render the failure_context dict into the markdown prompt the
    self_learning_agent expects. The agent's system prompt already
    documents its analysis steps; this is just the per-call payload."""
    parts: list[str] = []
    parts.append(
        f"# Failure Context · {failure_context.get('request_id', '?')}"
    )
    parts.append("")
    parts.append(
        f"**Final error:** {failure_context.get('final_error', '?')}"
    )
    parts.append(
        f"**Rework cycles:** {failure_context.get('retries', 0)}"
    )
    files = failure_context.get("related_files") or []
    if files:
        parts.append("**Files the agents touched (or referenced):**")
        for f in files:
            parts.append(f"  - `{f}`")
    parts.append("")
    traces = failure_context.get("agent_traces") or []
    if traces:
        parts.append("## Failed subtask outputs (most recent first)")
        parts.append("")
        for trace in traces:
            parts.append(trace)
            parts.append("")
    else:
        parts.append("_(no failed subtask output text captured)_")
        parts.append("")
    parts.append(
        "Now do the analysis steps in your system prompt: read existing "
        "lessons, decide whether this is a new pattern or a duplicate of "
        "an existing L<NN>, and either append a new lesson or report "
        "'No new lesson needed'. The lessons_writer tool runs its own "
        "jaccard dedup — duplicate-similarity matches will return "
        "DUPLICATE_SKIPPED; treat that as success per your prompt."
    )
    return "\n".join(parts)


# ── Handler factory ──────────────────────────────────────────────────────


def make_self_learning_handler(
    state: StateStore,
    agent_executor: Any,
    events: EventEmitter,
) -> Any:
    """Returns an async EventHandler bound to (state, executor, events).

    GOVERNANCE (HAI-48 / FR-062): self-learning is DELIBERATELY NOT coupled to
    governed mode. The analysis loop always runs automatically on a failure — it's
    a read-only background analysis that produces a lesson, not a state mutation on
    the product, so gating it behind a human confirm would just lose signal. The
    SEPARATE question of whether the produced lesson auto-promotes into the canonical
    doc or queues for human approval is controlled by the independent
    ``LESSONS_REVIEW_GATE`` flag (AET-13, default review-on) — that is the
    "flag to require approval" half of FR-062. So: loop automatic by default;
    output-promotion approval is a flag.

    Listens for `request.failed` events ONLY — every other event type
    is ignored cheaply. On hit, builds the failure context and fires
    `agent_executor.single_agent_call(agent_id='self_learning_agent', ...)`
    in a background task so the broadcast pipeline returns immediately.

    Per the agent's contract (silent background analysis), this does
    NOT create a Request or Subtask. The single_call path also doesn't
    emit `request.*` events — keeps the failure's audit log free of
    cross-talk from the analysis pass.
    """

    async def _handler(event_type: str, data: dict[str, Any]) -> None:
        # Cheap filter — bail fast on the 99% of events we don't care about.
        if event_type != "request.failed":
            return
        request_id = data.get("request_id") or ""
        if not request_id:
            return
        # Idempotency guard — drop if we're already analyzing this request.
        if request_id in _INFLIGHT_REQUESTS:
            logger.debug(
                "self_learning_skipped_inflight",
                request_id=request_id,
                hint="another request.failed event already kicked off analysis",
            )
            return

        async def _run_in_background() -> None:
            _INFLIGHT_REQUESTS.add(request_id)
            try:
                logger.info("self_learning_triggered", request_id=request_id)
                context = await _build_failure_context(
                    state, request_id, str(data.get("error") or ""),
                )
                prompt = _format_prompt(context)

                # single_agent_call is the right vehicle:
                #  - no Request/Subtask created (silent analysis)
                #  - no request.* events emitted (no UI noise)
                #  - the busy map (executor._busy_agents) DOES light up,
                #    so the Team Status page shows "self_learning_agent
                #    is working" while it runs
                result = await agent_executor.single_agent_call(
                    agent_id="self_learning_agent",
                    prompt=prompt,
                    label=f"Analyzing failure of {request_id}",
                )
                output = (result.get("text") or "")

                # Parse outcome from the agent's text output to emit the
                # right event. Three terminal verdicts — in priority order
                # so DUPLICATE_SKIPPED beats OK_WRITTEN when both prefixes
                # appear (the agent saw the tool's prefix and accurately
                # repeated it in its summary). The order matters because
                # `OK_WRITTEN` could appear earlier in the output as the
                # tool's response before the agent's own prose verdict.
                upper = output.upper()
                # DUPLICATE_SKIPPED detection — covers (a) the agent's
                # own prose decision per AET-12 ("already documented in
                # L<NN>") and (b) the tool's return prefix when its
                # jaccard guard caught a near-duplicate. Either path
                # emits the same event so subscribers don't need to
                # care which layer made the call.
                if "DUPLICATE_SKIPPED" in upper or "ALREADY DOCUMENTED" in upper:
                    match = re.search(r"L\d+", upper)
                    matched_lesson = match.group(0) if match else None
                    await events.emit("lessons.duplicate_skipped", {
                        "request_id": request_id,
                        "matched_lesson": matched_lesson,
                    })
                    logger.info(
                        "self_learning_duplicate_skipped",
                        request_id=request_id, matched_lesson=matched_lesson,
                    )
                # OK_WRITTEN_PENDING detection (AET-13) — covers the
                # default path with the review gate enabled. The lesson
                # was queued for human review, NOT promoted into the
                # canonical doc yet, so the event is named differently
                # so the UI can show a "needs review" badge instead of
                # a "new lesson learned" toast.
                elif "OK_WRITTEN_PENDING" in upper:
                    match = re.search(r"\bL(\d{1,3})\b", upper)
                    lesson_id = f"L{match.group(1)}" if match else "L?"
                    await events.emit("lessons.pending_review", {
                        "request_id": request_id,
                        "lesson_id": lesson_id,
                    })
                    logger.info(
                        "self_learning_lesson_pending_review",
                        request_id=request_id, lesson_id=lesson_id,
                    )
                # OK_WRITTEN detection — direct-to-canonical path
                # (review gate disabled via LESSONS_REVIEW_GATE=false).
                # Accepts EITHER form:
                #   - tool's raw return: "OK_WRITTEN: Lesson appended..."
                #   - agent's prose:    "Lesson L<NN> appended..."
                # Extracts the lesson_id by searching for `L<digits>`
                # anywhere in the text (the agent's prose template puts
                # it right after "Lesson "; the tool's prefix doesn't
                # include the lesson_id directly but the agent's summary
                # below it does).
                elif "OK_WRITTEN" in upper or (
                    "LESSON L" in upper and "APPENDED" in upper
                ):
                    match = re.search(r"\bL(\d{1,3})\b", upper)
                    lesson_id = f"L{match.group(1)}" if match else "L?"
                    await events.emit("lessons.added", {
                        "request_id": request_id,
                        "lesson_id": lesson_id,
                    })
                    logger.info(
                        "self_learning_lesson_added",
                        request_id=request_id, lesson_id=lesson_id,
                    )
                else:
                    await events.emit("lessons.no_new_pattern", {
                        "request_id": request_id,
                    })
                    logger.info(
                        "self_learning_no_new_pattern", request_id=request_id,
                    )
            except Exception as exc:  # noqa: BLE001
                # Broad-except is deliberate per the agent's contract —
                # self-learning must NEVER raise into the event loop or
                # the failure of one analysis breaks every future event
                # broadcast.
                logger.warning(
                    "self_learning_failed",
                    request_id=request_id, error=str(exc),
                )
            finally:
                _INFLIGHT_REQUESTS.discard(request_id)

        # Fire-and-forget — the EventEmitter handler awaits us but we
        # don't await the background task. Frees the event-broadcast
        # pipeline to deliver the next event immediately.
        asyncio.create_task(_run_in_background())

    return _handler
