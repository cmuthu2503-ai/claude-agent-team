"""Tests for the self-learning agent infrastructure.

Covers:
  T08.2  — LessonsWriterTool.read_lessons() returns non-empty string
  T08.3  — append then verify, then clean up
  T08.4  — DRY_RUN mode does not write to disk
  T08.5  — orchestrator _trigger_self_learning calls execute_agent with self_learning_agent

No LLM calls are made; all tests are purely file-system and orchestrator-unit level.
"""

import os
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.lessons_writer import LessonsWriterTool, LESSONS_FILE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_LESSON = "## L999 — TEST LESSON (auto-cleanup)\n**Signature:** `TEST_SIG`\n**Cause:** test\n**Fix:** test\n**Observed in:** REQ-TEST (2026-05-25)"


def _cleanup_test_lesson(marker: str = "L999") -> None:
    """Remove any appended test lesson lines from the lessons file."""
    if not LESSONS_FILE.exists():
        return
    text = LESSONS_FILE.read_text(encoding="utf-8")
    # Find the start of the test lesson block and trim everything from there.
    idx = text.find(f"## {marker} —")
    if idx == -1:
        return
    # Walk back to the nearest preceding newline so we don't leave a stray blank line.
    trimmed = text[:idx].rstrip()
    LESSONS_FILE.write_text(trimmed + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# T08.2 — read_lessons returns non-empty string with expected header
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_lessons_returns_string():
    """read_lessons() must return a non-empty string containing the doc header."""
    tool = LessonsWriterTool()
    result = await tool.read_lessons()
    assert isinstance(result, str)
    assert len(result) > 0
    # The doc always starts with this heading
    assert "Agent Lessons Learned" in result or "agent-lessons" in result.lower() or result.startswith("[lessons file")


# ---------------------------------------------------------------------------
# T08.3 — append a lesson, verify it's there, then clean up
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_then_cleanup(monkeypatch, tmp_path):
    """append_lesson() must persist the lesson; a duplicate call does not error.

    Isolated to temp files (monkeypatching the module-level path globals) so it
    NEVER touches the repo's real lessons docs — previously this test wrote L999
    into docs/agent-lessons-learned.pending.md and polluted the working tree.
    """
    import src.tools.lessons_writer as lw

    canonical = tmp_path / "agent-lessons-learned.md"
    pending = tmp_path / "agent-lessons-learned.pending.md"
    canonical.write_text("# Agent Lessons Learned\n", encoding="utf-8")
    pending.write_text("# Pending Review Queue\n", encoding="utf-8")
    monkeypatch.setattr(lw, "LESSONS_FILE", canonical)
    monkeypatch.setattr(lw, "PENDING_LESSONS_FILE", pending)

    tool = lw.LessonsWriterTool()

    # First append — fresh temp files, so the dedup guard finds no match.
    result = await tool.execute({"action": "append", "lesson_text": _TEST_LESSON})
    assert "lesson" in result.lower() or "ok_written" in result.lower() or "appended" in result.lower()

    # The lesson landed in one of the two files (pending when the review gate is on).
    written = canonical.read_text(encoding="utf-8") + pending.read_text(encoding="utf-8")
    assert "L999" in written and "TEST_SIG" in written, "Appended lesson not found"

    # Second call must not raise (dedup may now skip it — that's fine).
    result2 = await tool.execute({"action": "append", "lesson_text": _TEST_LESSON})
    assert isinstance(result2, str)


# ---------------------------------------------------------------------------
# T08.4 — DRY_RUN mode does not write to disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_mode():
    """When DRY_RUN=true the lesson must NOT be written to disk."""
    tool = LessonsWriterTool()

    # Ensure L998 is absent before the test
    if LESSONS_FILE.exists():
        assert "L998" not in LESSONS_FILE.read_text(encoding="utf-8"), (
            "Pre-condition failed: L998 already in lessons file"
        )

    with patch.dict(os.environ, {"DRY_RUN": "true"}):
        result = await tool.execute({
            "action": "append",
            "lesson_text": "## L998 — DRY RUN TEST\n**Signature:** `DRY_RUN`",
        })

    assert "DRY_RUN" in result or "dry" in result.lower(), (
        f"Expected DRY_RUN acknowledgement in output, got: {result}"
    )

    # File must not contain L998
    if LESSONS_FILE.exists():
        assert "L998" not in LESSONS_FILE.read_text(encoding="utf-8"), (
            "DRY_RUN guard failed: lesson was written to disk despite DRY_RUN=true"
        )


# ---------------------------------------------------------------------------
# T08.5 — orchestrator triggers self_learning_agent on failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_triggers_self_learning_on_fail():
    """The self-learning handler (AET-11) fires single_agent_call with
    'self_learning_agent' on a request.failed event.

    Self-learning moved from an Orchestrator method to an EventEmitter handler
    (`make_self_learning_handler`) registered in main.py; it runs the analysis
    in a background task via single_agent_call (silent — no Request/Subtask)."""
    from src.core.self_learning_trigger import make_self_learning_handler

    state = MagicMock()
    state.get_request = AsyncMock(return_value=MagicMock(
        description="x", task_type="feature_request", project_id=None,
    ))
    state.get_subtasks_for_request = AsyncMock(return_value=[])

    agent_executor = MagicMock()
    agent_executor.single_agent_call = AsyncMock(return_value={
        "text": "No new lesson needed — pattern already documented.",
    })

    events = MagicMock()
    events.emit = AsyncMock()

    handler = make_self_learning_handler(state, agent_executor, events)
    await handler("request.failed", {"request_id": "REQ-TEST-SLA", "error": "boom"})

    # The handler runs single_agent_call in a background task — yield to let it run.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if agent_executor.single_agent_call.called:
            break

    assert agent_executor.single_agent_call.called, "single_agent_call was never called"
    kwargs = agent_executor.single_agent_call.call_args.kwargs
    agent_arg = kwargs.get("agent_id")
    if agent_arg is None and agent_executor.single_agent_call.call_args.args:
        agent_arg = agent_executor.single_agent_call.call_args.args[0]
    assert agent_arg == "self_learning_agent", (
        f"Expected single_agent_call with 'self_learning_agent', got '{agent_arg}'"
    )


# ---------------------------------------------------------------------------
# T08.6 — schema() returns correct Anthropic tool definition shape
# ---------------------------------------------------------------------------

def test_lessons_writer_schema():
    """schema() must return a valid Anthropic tool definition dict."""
    tool = LessonsWriterTool()
    schema = tool.schema()
    assert schema["name"] == "lessons_writer"
    assert "description" in schema
    assert schema["input_schema"]["type"] == "object"
    props = schema["input_schema"]["properties"]
    assert "action" in props
    assert props["action"]["enum"] == ["read", "append"]
    assert "lesson_text" in props
    assert schema["input_schema"]["required"] == ["action"]


# ---------------------------------------------------------------------------
# T08.7 — execute() with unknown action returns error string (not raises)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_unknown_action_returns_error():
    """Unknown action must return an error string, not raise an exception."""
    tool = LessonsWriterTool()
    result = await tool.execute({"action": "delete"})
    assert "ERROR" in result or "unknown" in result.lower()


# ---------------------------------------------------------------------------
# T08.8 — execute append without lesson_text returns descriptive error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_append_missing_lesson_text():
    """append without lesson_text must return an error string."""
    tool = LessonsWriterTool()
    result = await tool.execute({"action": "append"})
    assert "ERROR" in result or "lesson_text" in result
