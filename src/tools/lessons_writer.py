"""Lessons writer tool — read from and append to docs/agent-lessons-learned.md.

Used exclusively by the ``self_learning_agent`` to persist new failure-pattern
lessons after a Request fails, so all code-producing agents automatically pick
up the lesson on their next invocation (via the _build_system_prompt injection
in src/agents/base.py).

Actions
-------
read    — Return the full text of the lessons file (so the agent can check
           whether the pattern is already documented before appending).
append  — Append a new lesson block to the end of the file.  The text must
           follow the canonical format:

               ## L<NN> — <one-line title>
               **Signature:** `<verbatim error / log line>`
               **Cause:** <what the agent was doing wrong>
               **Fix:** <concrete action>
               **Observed in:** <REQ-XXX>

AET-09 — idempotent dedup
-------------------------
Before writing, the tool tokenises the candidate lesson's Signature
line, compares it against the Signature of every existing L## entry
using Jaccard set similarity, and skips the write when the maximum
similarity is at or above ``JACCARD_THRESHOLD`` (default 0.6, override
via env LESSONS_WRITER_DUPE_THRESHOLD). Skipped writes return a
``DUPLICATE_SKIPPED:`` prefixed string so the calling agent can branch
without re-parsing prose. Prevents the self_learning_agent from
drafting near-identical lessons across rework cycles of the same
failure (the L17 drop-guard pattern, applied to its own output).

AET-13 — pending review gate
----------------------------
When ``LESSONS_REVIEW_GATE`` is enabled (default ``true``), the
``append`` action does NOT write directly to
``agent-lessons-learned.md``.  Instead, the lesson block is wrapped in
``<!-- pending-lesson:start … -->`` markers and appended to
``agent-lessons-learned.pending.md``, where it sits until a human hits
``POST /api/v1/lessons/{id}/approve`` (or ``…/reject``) via the
review-queue UI.  Dedup runs against the union of canonical + pending
signatures so the same pattern doesn't queue twice.

The agent receives ``OK_WRITTEN_PENDING:`` instead of ``OK_WRITTEN:`` in
this mode — same success semantics, just signals "queued, not yet
visible to code-writing agents". Set ``LESSONS_REVIEW_GATE=false`` to
restore direct-write behavior (e.g. during the smoke test in AET-14).

Return prefixes
---------------
Every response starts with a structured prefix so the agent can branch:
    OK_WRITTEN:         lesson successfully appended to canonical doc
    OK_WRITTEN_PENDING: lesson queued in pending review file (AET-13)
    DUPLICATE_SKIPPED:  structurally similar lesson already exists
    DRY_RUN:            DRY_RUN env set; lesson logged but not written
    ERROR:              failure (empty text, IO error, file missing)

DRY_RUN guard
-------------
When the environment variable ``DRY_RUN=true`` is set the append action logs
the lesson but does NOT write to disk.  Useful for integration-test runs that
import this module.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# Resolved once at import time so every call doesn't re-walk the parents chain.
_REPO_ROOT = Path(__file__).resolve().parents[2]
LESSONS_FILE = _REPO_ROOT / "docs" / "agent-lessons-learned.md"
PENDING_LESSONS_FILE = _REPO_ROOT / "docs" / "agent-lessons-learned.pending.md"

# AET-13 — pending review gate. When true (default), append() routes to
# the pending file. Disable via env for the AET-14 smoke test or for
# emergencies where a lesson must land in canonical immediately.
REVIEW_GATE_ENABLED = os.environ.get(
    "LESSONS_REVIEW_GATE", "true",
).strip().lower() not in {"false", "0", "no", "off"}

# Marker format for pending-file entries. Endpoints (lessons.py) parse
# these via regex to locate individual lesson blocks. KEEP THE SHAPE
# STABLE — anything that changes the marker syntax must update both
# this writer AND src/api/routes/lessons.py in the same commit (L23
# cross-layer label drift).
_PENDING_START_TMPL = (
    "<!-- pending-lesson:start id={lesson_id} request_id={request_id} "
    "created={created} -->"
)
_PENDING_END_TMPL = "<!-- pending-lesson:end id={lesson_id} -->"
_PENDING_BLOCK_RE = re.compile(
    r"<!--\s*pending-lesson:start\s+id=(?P<id>L\d{2,3})\s+"
    r"request_id=(?P<request_id>[^\s]+)\s+"
    r"created=(?P<created>[^\s]+)\s*-->\n"
    r"(?P<body>.*?)\n"
    r"<!--\s*pending-lesson:end\s+id=(?P=id)\s*-->",
    re.DOTALL,
)

# Header used to extract a lesson_id from a draft. Same regex as the
# canonical-doc parser, just hoisted here so we can find the id at
# write time to stamp the marker.
_LESSON_ID_RE = re.compile(r"^##\s+(L\d{2,3})\s*[—–-]", re.MULTILINE)

# ── Jaccard dedup configuration ───────────────────────────────────────────
# Threshold is the FRACTION OVERLAP (|A ∩ B| / |A ∪ B|) above which two
# signatures are considered duplicates. 0.6 is a deliberate middle ground:
#   - Too strict (0.9+): spam lessons get through because they share only
#     a few exact tokens
#   - Too loose (<0.4):  unrelated failure modes match because they share
#     a common library name or verb
# Tune via env if real-world failure data shows false positives/negatives.
JACCARD_THRESHOLD = float(os.environ.get("LESSONS_WRITER_DUPE_THRESHOLD", "0.6"))

# Tokens to ignore when comparing signatures. These appear in nearly
# every error trace so they don't carry discriminative information.
# Keep this list short — over-stripping erodes the signal.
_STOPLIST = frozenset({
    "the", "and", "for", "with", "from", "into", "this", "that",
    "when", "have", "will", "must", "error", "failed", "warning",
    "got", "expected", "found", "value", "result", "true", "false",
    "none", "null", "type", "object", "self", "args", "kwargs",
    "src", "test", "tests",
})

# Regex for "## L<NN> — <title>" headers used to split the lessons doc
# into per-lesson chunks. Tolerates "—" (em dash) AND "-" (hyphen) in
# the title separator since both have appeared historically.
_LESSON_HEADER_RE = re.compile(r"^##\s+(L\d{2,3})\s*[—–-]\s*(.+?)\s*$", re.MULTILINE)

# Regex for extracting the **Signature:** line out of a single lesson
# chunk. Handles both inline (`**Signature:** ...`) and section-header
# (`**Signature:**\n<paragraph>`) forms.
_SIGNATURE_RE = re.compile(
    r"\*\*Signature(?:\:)?\*\*\s*:?\s*(.+?)(?=\n\n|\n##|\n\*\*|\Z)",
    re.DOTALL | re.IGNORECASE,
)


# ── Signature extraction + similarity helpers (AET-09) ────────────────────


def _tokenize(text: str) -> set[str]:
    """Lowercase + extract alphanum tokens of length ≥4, drop stoplist.

    The ≥4 cutoff filters out noise tokens like 'a', 'in', 'on' that
    show up in every signature. Stripping the stoplist on top removes
    the structural words ('error', 'failed') that appear in nearly
    every signature regardless of class.
    """
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text or "")
    return {t.lower() for t in tokens if t.lower() not in _STOPLIST}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Standard Jaccard: |A ∩ B| / |A ∪ B|. Returns 0.0 when both
    sides are empty (no meaningful tokens — can't claim a duplicate)."""
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _extract_candidate_signature(lesson_text: str) -> str:
    """Pull the **Signature:** value from a lesson draft. Returns the
    raw signature text, or an empty string when the field is missing
    (in which case the dedup check skips and the write proceeds —
    we'd rather over-write than block a real new lesson)."""
    m = _SIGNATURE_RE.search(lesson_text or "")
    if not m:
        return ""
    return m.group(1).strip()


def _extract_existing_lesson_signatures(doc_text: str) -> list[tuple[str, str]]:
    """Walk every ``## L<NN> — <title>`` block in the lessons doc and
    extract (lesson_id, signature_text) for each. Skips lessons whose
    Signature line couldn't be parsed (graceful degradation: those
    lessons just don't participate in the dedup vote)."""
    if not doc_text:
        return []
    pairs: list[tuple[str, str]] = []
    # Find all lesson headers with their byte offsets so we can slice
    # the content between consecutive headers.
    headers = list(_LESSON_HEADER_RE.finditer(doc_text))
    for i, hdr in enumerate(headers):
        lesson_id = hdr.group(1)
        chunk_start = hdr.end()
        chunk_end = headers[i + 1].start() if i + 1 < len(headers) else len(doc_text)
        chunk = doc_text[chunk_start:chunk_end]
        sig = _extract_candidate_signature(chunk)
        if sig:
            pairs.append((lesson_id, sig))
    return pairs


def _find_duplicate(
    candidate_signature: str, existing_pairs: list[tuple[str, str]],
) -> tuple[str | None, float]:
    """Return (matched_lesson_id, similarity) for the highest-similarity
    existing lesson at or above JACCARD_THRESHOLD, or (None, 0.0)."""
    if not candidate_signature:
        return (None, 0.0)
    candidate_tokens = _tokenize(candidate_signature)
    if not candidate_tokens:
        return (None, 0.0)
    best_id: str | None = None
    best_score = 0.0
    for lesson_id, sig in existing_pairs:
        score = _jaccard_similarity(candidate_tokens, _tokenize(sig))
        if score > best_score:
            best_score = score
            best_id = lesson_id
    if best_score >= JACCARD_THRESHOLD:
        return (best_id, best_score)
    return (None, best_score)


# ── Tool ──────────────────────────────────────────────────────────────────


class LessonsWriterTool:
    """Read from and append to docs/agent-lessons-learned.md."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "lessons_writer",
            "description": (
                "Read or append to the shared agent-lessons-learned.md file. "
                "Use action='read' to retrieve existing lessons before deciding "
                "whether a new pattern is already documented. "
                "Use action='append' to persist a new lesson block so all "
                "code-producing agents avoid the same mistake next time."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "append"],
                        "description": (
                            "'read' — return the full lessons file text. "
                            "'append' — append lesson_text to the file."
                        ),
                    },
                    "lesson_text": {
                        "type": "string",
                        "description": (
                            "The lesson block to append. Required when action='append'. "
                            "Must start with '## L<NN> — <title>' and include "
                            "Signature, Cause, Fix, and Observed-in fields."
                        ),
                    },
                    "request_id": {
                        "type": "string",
                        "description": (
                            "Optional REQ-XXX that this lesson was learned from. "
                            "Stamped into the pending-lesson marker for traceability."
                        ),
                    },
                },
                "required": ["action"],
            },
        }

    async def read_lessons(self) -> str:
        """Return the full text of the lessons file, or a placeholder if missing."""
        if not LESSONS_FILE.exists():
            logger.warning(
                "lessons_file_missing",
                path=str(LESSONS_FILE),
            )
            return f"[lessons file not found at {LESSONS_FILE}]"
        text = LESSONS_FILE.read_text(encoding="utf-8")
        logger.info(
            "lessons_read",
            path=str(LESSONS_FILE),
            length=len(text),
        )
        return text

    async def append_lesson(
        self, lesson_text: str, request_id: str | None = None,
    ) -> str:
        """Append *lesson_text* to the lessons file (or pending file per AET-13).

        AET-09 — before writing, runs the jaccard-similarity dedup against
        every existing L## entry's Signature line. If the candidate
        signature overlaps any existing one by ≥JACCARD_THRESHOLD, the
        write is skipped and the response is prefixed
        ``DUPLICATE_SKIPPED:`` so the agent knows the lesson was already
        captured.

        AET-13 — when the review gate is enabled (default), the lesson
        block is wrapped in pending-lesson markers and appended to the
        pending review file instead of the canonical doc. Dedup compares
        against the union of canonical + pending signatures so the same
        pattern doesn't queue twice. Returns ``OK_WRITTEN_PENDING:`` on
        success.

        Returns a short confirmation string on success, an
        ``DUPLICATE_SKIPPED:`` notice on dedup hit, or an
        ``ERROR:`` description on failure. Never raises — the
        self_learning_agent should never crash due to a file-write error.
        """
        if not lesson_text or not lesson_text.strip():
            return "ERROR: lesson_text is empty — nothing appended."

        # Read canonical content once — we need it for both the dedup
        # check AND the subsequent write (if the dedup passes).
        if not LESSONS_FILE.exists():
            logger.error(
                "lessons_file_missing_on_append",
                path=str(LESSONS_FILE),
            )
            return f"ERROR: lessons file not found at {LESSONS_FILE}. Cannot append."

        try:
            canonical = LESSONS_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("lessons_read_failed", error=str(exc), path=str(LESSONS_FILE))
            return f"ERROR: could not read {LESSONS_FILE}: {exc}"

        # Also read pending file so dedup catches "already queued for review".
        pending_text = ""
        if REVIEW_GATE_ENABLED and PENDING_LESSONS_FILE.exists():
            try:
                pending_text = PENDING_LESSONS_FILE.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "lessons_pending_read_failed",
                    error=str(exc), path=str(PENDING_LESSONS_FILE),
                )

        # AET-09 dedup — jaccard similarity on signature tokens.
        # Union of canonical + pending signatures so queued lessons also
        # block duplicates (AET-13).
        candidate_sig = _extract_candidate_signature(lesson_text)
        if candidate_sig:
            existing_pairs = _extract_existing_lesson_signatures(canonical)
            if pending_text:
                existing_pairs += _extract_existing_lesson_signatures(pending_text)
            matched_id, score = _find_duplicate(candidate_sig, existing_pairs)
            if matched_id is not None:
                logger.info(
                    "lessons_append_duplicate_skipped",
                    matched_lesson_id=matched_id,
                    similarity=round(score, 3),
                    threshold=JACCARD_THRESHOLD,
                    candidate_signature_preview=candidate_sig[:120],
                )
                return (
                    f"DUPLICATE_SKIPPED: matches existing {matched_id} "
                    f"(similarity {score:.2f} ≥ threshold {JACCARD_THRESHOLD:.2f}). "
                    f"No write performed — the failure pattern is already documented."
                )
        else:
            # Missing **Signature:** field — log but proceed (we don't
            # want to block a real new lesson over a formatting nit).
            # The agent's prompt should always include this field.
            logger.warning(
                "lessons_append_no_signature_field",
                hint="lesson_text has no **Signature:** field; dedup skipped",
                lesson_preview=lesson_text[:120],
            )

        # DRY_RUN guard: log but do not write. Applied AFTER the dedup
        # check so dry-run logs also show the dedup decision.
        if os.environ.get("DRY_RUN", "").lower() == "true":
            logger.info(
                "lessons_append_dry_run",
                lesson_preview=lesson_text[:120],
            )
            return f"DRY_RUN: lesson not written to disk ({len(lesson_text)} chars logged)."

        # AET-13 — route to pending file when gate is on.
        if REVIEW_GATE_ENABLED:
            return await self._append_to_pending(lesson_text, request_id)

        # Direct write to canonical (gate disabled).
        try:
            separator = "\n\n" if canonical.endswith("\n") else "\n\n\n"
            updated = canonical + separator + lesson_text.strip() + "\n"
            LESSONS_FILE.write_text(updated, encoding="utf-8")

            logger.info(
                "lessons_appended",
                path=str(LESSONS_FILE),
                lesson_preview=lesson_text[:120],
                total_length=len(updated),
            )
            return (
                f"OK_WRITTEN: Lesson appended successfully to {LESSONS_FILE.name} "
                f"({len(lesson_text)} chars added, file now {len(updated)} chars)."
            )
        except OSError as exc:
            logger.error(
                "lessons_append_failed",
                error=str(exc),
                path=str(LESSONS_FILE),
            )
            return f"ERROR: could not write to {LESSONS_FILE}: {exc}"

    async def _append_to_pending(
        self, lesson_text: str, request_id: str | None,
    ) -> str:
        """Wrap a lesson block in pending markers and append it to the
        pending review file. Stamps lesson_id (parsed from the draft),
        request_id (from the caller — typically the failing REQ-XXX),
        and an ISO-8601 created-timestamp into the start marker so the
        approve/reject endpoints can find it later.
        """
        if not PENDING_LESSONS_FILE.exists():
            logger.error(
                "pending_file_missing",
                path=str(PENDING_LESSONS_FILE),
            )
            return (
                f"ERROR: pending file not found at {PENDING_LESSONS_FILE}. "
                f"Run the AET-13 migration to create it."
            )

        # Parse the lesson_id from the draft. If absent, generate one
        # based on the highest existing id across both files +1.
        id_match = _LESSON_ID_RE.search(lesson_text)
        lesson_id = id_match.group(1) if id_match else self._next_lesson_id()

        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        req_id = (request_id or "unknown").replace(" ", "_")

        start_marker = _PENDING_START_TMPL.format(
            lesson_id=lesson_id, request_id=req_id, created=created,
        )
        end_marker = _PENDING_END_TMPL.format(lesson_id=lesson_id)

        block = f"{start_marker}\n{lesson_text.strip()}\n{end_marker}\n"

        try:
            existing = PENDING_LESSONS_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error(
                "pending_read_failed", error=str(exc),
                path=str(PENDING_LESSONS_FILE),
            )
            return f"ERROR: could not read {PENDING_LESSONS_FILE}: {exc}"

        try:
            separator = "\n" if existing.endswith("\n") else "\n\n"
            updated = existing + separator + block
            PENDING_LESSONS_FILE.write_text(updated, encoding="utf-8")
        except OSError as exc:
            logger.error(
                "pending_write_failed", error=str(exc),
                path=str(PENDING_LESSONS_FILE),
            )
            return f"ERROR: could not write to {PENDING_LESSONS_FILE}: {exc}"

        logger.info(
            "lesson_queued_for_review",
            lesson_id=lesson_id, request_id=req_id,
            path=str(PENDING_LESSONS_FILE),
            block_chars=len(block),
        )
        return (
            f"OK_WRITTEN_PENDING: Lesson {lesson_id} queued for human review in "
            f"{PENDING_LESSONS_FILE.name} ({len(block)} chars). "
            f"Approve via POST /api/v1/lessons/{lesson_id}/approve to promote it "
            f"into the canonical doc."
        )

    def _next_lesson_id(self) -> str:
        """Fallback id allocator when a draft omits its own ## L<NN> header.
        Walks both files for the highest L<NN> and returns L<NN+1>. Defaults
        to L01 on a virgin install."""
        max_n = 0
        for f in (LESSONS_FILE, PENDING_LESSONS_FILE):
            try:
                text = f.read_text(encoding="utf-8") if f.exists() else ""
            except OSError:
                continue
            for m in _LESSON_ID_RE.finditer(text):
                try:
                    n = int(m.group(1)[1:])
                    if n > max_n:
                        max_n = n
                except ValueError:
                    continue
        return f"L{max_n + 1:02d}"

    async def execute(self, params: dict[str, Any]) -> str:
        action = params.get("action", "").strip().lower()

        if action == "read":
            return await self.read_lessons()

        if action == "append":
            lesson_text = params.get("lesson_text", "")
            if not lesson_text:
                return (
                    "ERROR: 'lesson_text' is required for action='append'. "
                    "Provide the full lesson block starting with '## L<NN> — <title>'."
                )
            request_id = params.get("request_id") or None
            return await self.append_lesson(lesson_text, request_id=request_id)

        return (
            f"ERROR: unknown action '{action}'. "
            "Valid actions are 'read' and 'append'."
        )
