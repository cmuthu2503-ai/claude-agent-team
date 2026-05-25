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
async def test_append_then_cleanup():
    """append_lesson() must persist the lesson to disk; duplicate call does not error."""
    tool = LessonsWriterTool()

    # Ensure clean state
    _cleanup_test_lesson("L999")

    try:
        # First append
        result = await tool.execute({"action": "append", "lesson_text": _TEST_LESSON})
        assert "appended" in result.lower() or "lesson" in result.lower()

        # Verify on disk
        text = LESSONS_FILE.read_text(encoding="utf-8")
        assert "L999" in text, "Appended lesson not found in file"
        assert "TEST_SIG" in text

        # Second call (idempotency check — should not raise, just append again or succeed)
        result2 = await tool.execute({"action": "append", "lesson_text": _TEST_LESSON})
        assert isinstance(result2, str)
    finally:
        # Always clean up — remove ALL occurrences of the test lesson
        if LESSONS_FILE.exists():
            raw = LESSONS_FILE.read_text(encoding="utf-8")
            # Remove from first occurrence to end of that block
            idx = raw.find("## L999 —")
            if idx != -1:
                LESSONS_FILE.write_text(raw[:idx].rstrip() + "\n", encoding="utf-8")


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
    """_trigger_self_learning must call execute_agent with 'self_learning_agent'."""
    from src.core.orchestrator import Orchestrator

    # Minimal mock setup — we only need to verify execute_agent is called
    mock_execute = AsyncMock(return_value={
        "outputs": {"self_learning_agent": "No new lesson needed."},
        "artifacts": [],
        "text": "No new lesson needed — pattern already documented.",
        "self_learning_agent_output": "No new lesson needed — pattern already documented.",
    })

    orch = object.__new__(Orchestrator)
    orch.execute_agent = mock_execute
    orch.events = MagicMock()
    orch.events.emit = AsyncMock()

    await orch._trigger_self_learning("REQ-TEST-SLA")

    # execute_agent must have been called with self_learning_agent as first positional arg
    assert mock_execute.called, "execute_agent was never called"
    call_args = mock_execute.call_args
    agent_arg = call_args[0][0] if call_args[0] else call_args[1].get("agent_id")
    assert agent_arg == "self_learning_agent", (
        f"Expected execute_agent to be called with 'self_learning_agent', got '{agent_arg}'"
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
