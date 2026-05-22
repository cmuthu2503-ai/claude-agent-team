"""Regression tests for the drop-guard same-content loop detector.

Pins the fix that closed T-103e9025's failure class — the agent emitted
the same 275-line shrink of a 764-line file three rework cycles in a
row. The drop guard rejected each one with the same message; the
agent ignored the message and re-emitted byte-identical content.

The detector remembers the rejected content's sha256 per
(request_id, file_path) and escalates with a louder message when the
same hash comes back. Successful materialize clears the cache so a
re-dispatch starts fresh.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.code_writer import CodeWriteError, CodeWriter


@pytest.fixture
def writer(tmp_path: Path) -> CodeWriter:
    return CodeWriter(state=None, project_root=str(tmp_path))  # type: ignore[arg-type]


# A realistic file that triggers the drop guard:
_OLD_TEXT = "\n".join(f"line {i}" for i in range(1, 31))  # 30 lines


def _shrunk_emission(n: int) -> str:
    """Build a deterministic short emission of `n` lines."""
    return "\n".join(f"new {i}" for i in range(1, n + 1))


# ── First rejection: standard drop-guard message ───────────────────────────


def test_first_drop_rejection_uses_standard_message(writer: CodeWriter) -> None:
    """Cycle 0 of a drop-guard rejection emits the original three-options
    message (MERGE / SURGICAL / SPLIT), not the loop-detection message."""
    short = _shrunk_emission(5)  # 5 lines vs 30 lines on disk
    with pytest.raises(CodeWriteError) as exc:
        writer._validate_safe_overwrite(
            file_path="src/foo.py",
            agent_id="backend_specialist",
            old_text=_OLD_TEXT,
            new_text=short,
            request_id="REQ-TEST01",
        )
    msg = str(exc.value)
    assert "line count dropped" in msg
    # Standard message lists the three options
    assert "MERGE" in msg
    assert "SURGICAL" in msg
    assert "SPLIT" in msg
    # NOT the escalated loop-detection wording
    assert "LOOP" not in msg


# ── Second rejection with same hash: escalated message ─────────────────────


def test_second_identical_rejection_triggers_loop_detection(writer: CodeWriter) -> None:
    """Cycle 1 with byte-identical content emits the LOUDER loop message."""
    short = _shrunk_emission(5)
    # Cycle 0 — standard rejection
    with pytest.raises(CodeWriteError):
        writer._validate_safe_overwrite(
            file_path="src/foo.py",
            agent_id="backend_specialist",
            old_text=_OLD_TEXT,
            new_text=short,
            request_id="REQ-TEST02",
        )
    # Cycle 1 — same bytes → escalation
    with pytest.raises(CodeWriteError) as exc:
        writer._validate_safe_overwrite(
            file_path="src/foo.py",
            agent_id="backend_specialist",
            old_text=_OLD_TEXT,
            new_text=short,
            request_id="REQ-TEST02",
        )
    msg = str(exc.value)
    assert "LOOP" in msg or "BYTE-IDENTICAL" in msg
    assert "search_replace" in msg
    # Calls out the gap between current-on-disk and last-emitted line
    # counts so the agent knows what to do. The exact numbers depend on
    # newline counting (`text.count("\n")` vs len(splitlines())), so we
    # just assert that BOTH numbers appear somewhere in the message.
    # _OLD_TEXT has 30 line strings joined → 29 newlines.
    # short=_shrunk_emission(5) has 5 line strings joined → 4 newlines.
    assert "29" in msg
    assert "4" in msg


# ── Different content on cycle 1: no escalation (legit retry) ──────────────


def test_different_content_on_retry_is_not_a_loop(writer: CodeWriter) -> None:
    """If the agent CHANGES its emission (even slightly), it's a legit
    retry — escalation message would be wrong here."""
    short_a = _shrunk_emission(5)
    short_b = _shrunk_emission(6)
    # Cycle 0
    with pytest.raises(CodeWriteError):
        writer._validate_safe_overwrite(
            file_path="src/foo.py",
            agent_id="backend_specialist",
            old_text=_OLD_TEXT,
            new_text=short_a,
            request_id="REQ-TEST03",
        )
    # Cycle 1 — different bytes → standard message, NOT escalation
    with pytest.raises(CodeWriteError) as exc:
        writer._validate_safe_overwrite(
            file_path="src/foo.py",
            agent_id="backend_specialist",
            old_text=_OLD_TEXT,
            new_text=short_b,
            request_id="REQ-TEST03",
        )
    assert "LOOP" not in str(exc.value)


# ── Cache is scoped per (request_id, file_path) ─────────────────────────────


def test_cache_isolated_per_request(writer: CodeWriter) -> None:
    """The same shrunken bytes for a DIFFERENT request_id must not
    trigger the loop detector — it's a fresh dispatch, no history."""
    short = _shrunk_emission(5)
    with pytest.raises(CodeWriteError):
        writer._validate_safe_overwrite(
            file_path="src/foo.py", agent_id="x",
            old_text=_OLD_TEXT, new_text=short,
            request_id="REQ-FIRST",
        )
    with pytest.raises(CodeWriteError) as exc:
        writer._validate_safe_overwrite(
            file_path="src/foo.py", agent_id="x",
            old_text=_OLD_TEXT, new_text=short,
            request_id="REQ-SECOND",  # different request — no loop
        )
    assert "LOOP" not in str(exc.value)


def test_cache_isolated_per_file_path(writer: CodeWriter) -> None:
    """Same hash but different file path = not a loop."""
    short = _shrunk_emission(5)
    with pytest.raises(CodeWriteError):
        writer._validate_safe_overwrite(
            file_path="src/foo.py", agent_id="x",
            old_text=_OLD_TEXT, new_text=short,
            request_id="REQ-SAME",
        )
    with pytest.raises(CodeWriteError) as exc:
        writer._validate_safe_overwrite(
            file_path="src/bar.py",  # different file
            agent_id="x",
            old_text=_OLD_TEXT, new_text=short,
            request_id="REQ-SAME",
        )
    assert "LOOP" not in str(exc.value)


# ── No request_id: detector is opt-in via the keyed cache ──────────────────


def test_no_request_id_disables_loop_detector(writer: CodeWriter) -> None:
    """If the caller doesn't pass request_id, both calls get the standard
    rejection — no loop detection. This preserves behaviour for tests /
    direct callers that don't have a request context."""
    short = _shrunk_emission(5)
    for _ in range(3):
        with pytest.raises(CodeWriteError) as exc:
            writer._validate_safe_overwrite(
                file_path="src/foo.py", agent_id="x",
                old_text=_OLD_TEXT, new_text=short,
                # no request_id
            )
        # Always the standard message, never escalation
        assert "LOOP" not in str(exc.value)
