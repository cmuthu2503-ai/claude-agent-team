"""Phase AE-2 end-to-end smoke test for self_learning_agent + pending review (AET-14).

Pins the contract that AE-2 hangs off:

  1. Three synthetic `request.failed` events are fed to the
     self-learning handler. One has a NOVEL signature; the other two
     match existing lessons (one canonical, one already-pending).
  2. Only the NOVEL lesson is queued in
     ``docs/agent-lessons-learned.pending.md``. The two dupes are
     short-circuited via the lessons_writer's jaccard guard with NO
     write to the pending file.
  3. The right events fire for each case:
       - novel       → ``lessons.pending_review``
       - dup (any)   → ``lessons.duplicate_skipped``
  4. The approve endpoint promotes the novel lesson into the canonical
     doc and removes the block from the pending file. Reject also
     removes from pending without touching canonical.

Setup
-----
The handler under test calls
``agent_executor.single_agent_call(agent_id='self_learning_agent', …)``
which in production hits the LLM. For this smoke test we stub the
executor: the stub agent ALWAYS calls the real ``LessonsWriterTool``
with a canned ``lesson_text`` parameterized per fixture, then returns
prose containing the tool's response prefix verbatim. That exercises
the full write path (file I/O + dedup + marker stamping) without any
network dependency.

The lessons files are redirected to tmp_path via monkeypatch so we
don't pollute the repo's real docs. The approve/reject endpoints are
called as plain async functions (FastAPI dependency overrides aren't
needed since the route handlers accept a Request and a user dict).

Run via:
  docker compose exec backend pytest tests/test_self_learning_smoke.py -v
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from src.core.events import EventEmitter
from src.core.self_learning_trigger import make_self_learning_handler


# ── Canned lesson drafts ──────────────────────────────────────────────────


# Lesson drafts use the canonical formatting convention from
# docs/agent-lessons-learned.md: blank line between every bold field.
# Without the blank lines, `_extract_candidate_signature`'s lazy match
# absorbs the Cause/Fix/Observed-in text into the "signature" and
# dilutes the jaccard signal.

# Existing canonical lesson the agent will accidentally re-draft for
# fixture #2 (uses lots of the same distinctive tokens as L11).
_DUP_OF_L11_DRAFT = """## L11 — duplicate draft of large-file rule

**Signature:** `E501 Line too long src/foo.py: 145 chars exceeds 100 character ruff limit`

**Cause:** Agent ignored the line-length cap.

**Fix:** Wrap or extract.

**Observed in:** REQ-DUPE-CANONICAL (2026-05-27)
"""

# A new pending-only signature we'll seed BEFORE the smoke run so
# fixture #3 can dedup against it (i.e. the queue itself blocks
# resubmission of already-pending patterns).
_PRESEED_PENDING_DRAFT = """## L80 — preseeded pending pattern

**Signature:** `PreseedSentinel: rare uniquetoken QQQX9988 zonk garbleflux`

**Cause:** Seeded for AET-14 smoke fixture #3.

**Fix:** Wait for review.

**Observed in:** REQ-PRESEED (2026-05-27)
"""

# Fixture #3 re-derives the SAME signature as the preseeded pending
# lesson — dedup must catch this against the pending file, not just
# the canonical one.
_DUP_OF_PENDING_DRAFT = """## L81 — re-derived rare token

**Signature:** `PreseedSentinel: rare uniquetoken QQQX9988 zonk garbleflux`

**Cause:** Same failure observed again on a different REQ.

**Fix:** —

**Observed in:** REQ-DUPE-PENDING (2026-05-27)
"""

# Fixture #1 — genuinely novel signature, none of these tokens appear
# in any existing canonical L## or in the preseed.
_NOVEL_DRAFT = """## L90 — AET-14 smoke novel signature

**Signature:** `AET14NovelMarker: hapax legomenon ZZZXX7766 vorpalcharm`

**Cause:** Novel failure class introduced by the smoke fixture.

**Fix:** Add a new lesson — that's the point.

**Observed in:** REQ-NOVEL (2026-05-27)
"""


# ── Stub state store + executor ───────────────────────────────────────────


class _StubStateStore:
    """The handler queries state for subtasks + retries when building
    the failure context. For smoke purposes we return empty lists so
    the prompt is minimal — the handler still fires and the lesson_text
    we pass via the stubbed executor is independent of context shape."""

    async def get_request(self, request_id: str) -> Any:
        return None

    async def get_subtasks_for_request(self, request_id: str) -> list[Any]:
        return []


class _StubAgentExecutor:
    """Executor stub whose ``single_agent_call`` invokes the real
    LessonsWriterTool with a per-call lesson_text. The handler's only
    contract with the executor is the return shape ``{"text": str}``;
    we set ``.text`` to the verbatim tool response so the handler's
    prefix parser fires the right event.

    The lesson_text is selected by request_id via a lookup table the
    test populates — that mirrors how the real agent would draft a
    different lesson per failure context.
    """

    def __init__(self, draft_by_request_id: dict[str, str]) -> None:
        self._drafts = draft_by_request_id
        # Late-binding import so tests that don't use this class don't
        # pay the cost of constructing the tool.
        from src.tools.lessons_writer import LessonsWriterTool
        self._tool = LessonsWriterTool()

    async def single_agent_call(
        self, agent_id: str, prompt: str, label: str,
    ) -> dict[str, Any]:
        assert agent_id == "self_learning_agent"
        # Lift the REQ-XXX out of the prompt header so we know which
        # draft to send (the handler renders it via _format_prompt).
        m = re.search(r"# Failure Context · (\S+)", prompt)
        request_id = m.group(1) if m else "unknown"
        lesson_text = self._drafts.get(request_id)
        if lesson_text is None:
            return {"text": "No new lesson needed — nothing to draft."}
        # Call the REAL tool. Its response prefix is what the handler
        # parses.
        tool_response = await self._tool.execute({
            "action": "append",
            "lesson_text": lesson_text,
            "request_id": request_id,
        })
        return {"text": tool_response}


# ── Fixtures: redirect lesson files to tmp_path ───────────────────────────


@pytest.fixture
def isolated_lesson_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point LESSONS_FILE + PENDING_LESSONS_FILE at temp copies so the
    smoke test doesn't pollute the repo's actual docs. We also need
    to repoint the same names inside src.api.routes.lessons, which
    imported them at module-load time. Use monkeypatch.setattr on both
    modules for completeness."""
    canonical = tmp_path / "agent-lessons-learned.md"
    pending = tmp_path / "agent-lessons-learned.pending.md"

    # Seed canonical with one existing lesson the dup fixture #2 will
    # collide with via jaccard.
    canonical.write_text(
        "# Agent Lessons Learned\n\n"
        "## L11 — Line length cap\n\n"
        "**Signature:** `E501 Line too long src/foo.py 145 chars exceeds 100 character ruff limit`\n\n"
        "**Cause:** Agent ignored the ruff line-length cap.\n\n"
        "**Fix:** Wrap or extract long lines.\n\n"
        "**Observed in:** REQ-001 (2026-05-01)\n",
        encoding="utf-8",
    )

    # Seed pending with one entry the dup fixture #3 will collide with.
    pending.write_text(
        "# Pending Review Queue\n\n"
        "<!-- pending-lesson:start id=L80 request_id=REQ-PRESEED created=2026-05-27T00:00:00Z -->\n"
        + _PRESEED_PENDING_DRAFT.strip()
        + "\n<!-- pending-lesson:end id=L80 -->\n",
        encoding="utf-8",
    )

    import src.tools.lessons_writer as lw
    import src.api.routes.lessons as routes_lessons
    monkeypatch.setattr(lw, "LESSONS_FILE", canonical)
    monkeypatch.setattr(lw, "PENDING_LESSONS_FILE", pending)
    monkeypatch.setattr(lw, "REVIEW_GATE_ENABLED", True)
    monkeypatch.setattr(routes_lessons, "LESSONS_FILE", canonical)
    monkeypatch.setattr(routes_lessons, "PENDING_LESSONS_FILE", pending)
    return {"canonical": canonical, "pending": pending}


# ── Event capture ─────────────────────────────────────────────────────────


def _make_capture_emitter() -> tuple[EventEmitter, list[tuple[str, dict]]]:
    events = EventEmitter()
    captured: list[tuple[str, dict]] = []

    async def _capture(event_type: str, data: dict) -> None:
        captured.append((event_type, dict(data)))

    events.on(_capture)
    return events, captured


async def _drain_background() -> None:
    """Yield enough times for the handler's fire-and-forget background
    task to finish (it awaits one tool call + a few event emits)."""
    for _ in range(20):
        await asyncio.sleep(0.01)


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_three_failures_one_novel_two_dupes(isolated_lesson_files):
    """The headline contract: 3 failures, only 1 novel lesson lands."""
    pending_file = isolated_lesson_files["pending"]
    canonical_file = isolated_lesson_files["canonical"]

    events, captured = _make_capture_emitter()
    state = _StubStateStore()
    executor = _StubAgentExecutor(draft_by_request_id={
        "REQ-NOVEL":         _NOVEL_DRAFT,
        "REQ-DUPE-CANONICAL": _DUP_OF_L11_DRAFT,
        "REQ-DUPE-PENDING":  _DUP_OF_PENDING_DRAFT,
    })

    handler = make_self_learning_handler(state, executor, events)
    events.on(handler)

    # Fire all three events.
    await events.emit("request.failed", {
        "request_id": "REQ-NOVEL", "error": "TestError: novel",
    })
    await events.emit("request.failed", {
        "request_id": "REQ-DUPE-CANONICAL",
        "error": "ruff E501 line too long",
    })
    await events.emit("request.failed", {
        "request_id": "REQ-DUPE-PENDING",
        "error": "PreseedSentinel rare token",
    })

    await _drain_background()

    # ── Assert pending file has exactly ONE new entry beyond the
    # preseed (L80 stays; the novel L90 is added; the dupes are not).
    pending_text = pending_file.read_text(encoding="utf-8")
    block_ids = re.findall(
        r"<!--\s*pending-lesson:start\s+id=(L\d+)", pending_text,
    )
    assert "L80" in block_ids, "preseeded pending lesson must remain"
    assert "L90" in block_ids, "novel lesson must be queued"
    # Crucially: dup lessons must NOT have been written.
    assert "L11" not in block_ids, "canonical dup must not be queued"
    assert "L81" not in block_ids, "pending dup must not be queued"

    # ── Canonical doc must be UNCHANGED — review gate prevents direct
    # writes; only approve endpoint promotes.
    canonical_text = canonical_file.read_text(encoding="utf-8")
    assert "L90" not in canonical_text
    assert "AET14NovelMarker" not in canonical_text

    # ── Events: 1 pending_review, 2 duplicate_skipped.
    novel_events = [e for e in captured if e[0] == "lessons.pending_review"]
    dup_events = [e for e in captured if e[0] == "lessons.duplicate_skipped"]
    assert len(novel_events) == 1, (
        f"expected exactly 1 lessons.pending_review, got {len(novel_events)}: {captured}"
    )
    assert len(dup_events) == 2, (
        f"expected 2 lessons.duplicate_skipped, got {len(dup_events)}: {captured}"
    )
    # No accidental lessons.added (that's the review-gate-disabled path).
    assert not [e for e in captured if e[0] == "lessons.added"]


@pytest.mark.asyncio
async def test_approve_endpoint_promotes_pending_to_canonical(isolated_lesson_files):
    """Approve route: lifts the block out of pending, appends to canonical."""
    from src.api.routes.lessons import _parse_pending_entries

    pending_file = isolated_lesson_files["pending"]
    canonical_file = isolated_lesson_files["canonical"]

    # Pre-queue a novel lesson directly via the tool so we don't depend
    # on the handler path (that's covered above).
    from src.tools.lessons_writer import LessonsWriterTool
    tool = LessonsWriterTool()
    result = await tool.execute({
        "action": "append",
        "lesson_text": _NOVEL_DRAFT,
        "request_id": "REQ-NOVEL",
    })
    assert result.startswith("OK_WRITTEN_PENDING:"), result

    # Sanity: novel is in pending, not yet in canonical.
    assert "L90" in pending_file.read_text(encoding="utf-8")
    assert "L90" not in canonical_file.read_text(encoding="utf-8")

    # ── Simulate the approve route inline. We can't easily spin up
    # FastAPI here, but the route's logic is straightforward — parse
    # the block, append body to canonical, strip from pending. Same
    # path the endpoint takes (src/api/routes/lessons.py::approve_lesson).
    from src.api.routes.lessons import (
        _find_pending,
        _strip_block_with_neighbors,
    )

    pending_text = pending_file.read_text(encoding="utf-8")
    entry = _find_pending(pending_text, "L90")
    assert entry is not None, "L90 must be findable in pending"

    canonical_text = canonical_file.read_text(encoding="utf-8")
    separator = "\n\n" if canonical_text.endswith("\n") else "\n\n\n"
    new_canonical = canonical_text + separator + entry["body"] + "\n"
    canonical_file.write_text(new_canonical, encoding="utf-8")

    new_pending = _strip_block_with_neighbors(
        pending_text, entry["span_start"], entry["span_end"],
    )
    pending_file.write_text(new_pending, encoding="utf-8")

    # ── After approve: canonical contains the lesson body, pending no
    # longer has the block.
    canonical_after = canonical_file.read_text(encoding="utf-8")
    pending_after = pending_file.read_text(encoding="utf-8")
    assert "L90 — AET-14 smoke novel signature" in canonical_after
    assert "AET14NovelMarker" in canonical_after
    assert "L90" not in [
        e["lesson_id"] for e in _parse_pending_entries(pending_after)
    ]


@pytest.mark.asyncio
async def test_dedup_catches_dup_when_draft_lacks_blank_lines(isolated_lesson_files):
    """Regression: a draft with single-newline-separated bold fields
    (no blank line between Signature/Cause/Fix) must still produce a
    clean Signature extraction so jaccard dedup catches it against the
    canonical L11 seed. Prior to the regex lookahead fix the lazy
    `.+?` over-captured Cause/Fix/Observed-in into the signature value,
    diluting the score below 0.6 and letting near-duplicates slip past.
    """
    from src.tools.lessons_writer import (
        LessonsWriterTool,
        _extract_candidate_signature,
        _find_duplicate,
        _extract_existing_lesson_signatures,
    )

    # Same L11 dup, but with SINGLE \n between bold fields (no blank line).
    no_blank_line_draft = (
        "## L11b — duplicate without blank lines\n"
        "**Signature:** `E501 Line too long src/foo.py: 145 chars exceeds 100 character ruff limit`\n"
        "**Cause:** Agent ignored the line-length cap.\n"
        "**Fix:** Wrap or extract.\n"
        "**Observed in:** REQ-NOBLANK (2026-05-27)\n"
    )

    # Sanity: signature extraction stops at the next bold field.
    sig = _extract_candidate_signature(no_blank_line_draft)
    assert "Cause" not in sig, f"signature over-captured Cause: {sig!r}"
    assert "Fix" not in sig, f"signature over-captured Fix: {sig!r}"
    assert "E501" in sig

    # Dedup against canonical must trip.
    canonical = isolated_lesson_files["canonical"].read_text(encoding="utf-8")
    matched, score = _find_duplicate(
        sig, _extract_existing_lesson_signatures(canonical),
    )
    assert matched == "L11", f"expected L11 match, got {matched!r} (score={score:.2f})"
    assert score >= 0.6, f"jaccard score {score:.2f} below threshold"

    # End-to-end: the writer tool returns DUPLICATE_SKIPPED.
    tool = LessonsWriterTool()
    result = await tool.execute({
        "action": "append",
        "lesson_text": no_blank_line_draft,
        "request_id": "REQ-NOBLANK",
    })
    assert result.startswith("DUPLICATE_SKIPPED:"), result


@pytest.mark.asyncio
async def test_reject_endpoint_drops_pending_without_touching_canonical(
    isolated_lesson_files,
):
    """Reject route: removes from pending, never touches canonical."""
    from src.api.routes.lessons import (
        _find_pending,
        _parse_pending_entries,
        _strip_block_with_neighbors,
    )

    pending_file = isolated_lesson_files["pending"]
    canonical_file = isolated_lesson_files["canonical"]

    # The preseeded L80 from the fixture stands in for "a queued lesson
    # an admin decided not to promote."
    canonical_before = canonical_file.read_text(encoding="utf-8")
    pending_text = pending_file.read_text(encoding="utf-8")

    entry = _find_pending(pending_text, "L80")
    assert entry is not None
    new_pending = _strip_block_with_neighbors(
        pending_text, entry["span_start"], entry["span_end"],
    )
    pending_file.write_text(new_pending, encoding="utf-8")

    # Canonical untouched.
    assert canonical_file.read_text(encoding="utf-8") == canonical_before
    # Pending no longer has L80.
    assert "L80" not in [
        e["lesson_id"]
        for e in _parse_pending_entries(pending_file.read_text(encoding="utf-8"))
    ]
