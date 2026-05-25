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

DRY_RUN guard
-------------
When the environment variable ``DRY_RUN=true`` is set the append action logs
the lesson but does NOT write to disk.  Useful for integration-test runs that
import this module.
"""

import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# Resolved once at import time so every call doesn't re-walk the parents chain.
_REPO_ROOT = Path(__file__).resolve().parents[2]
LESSONS_FILE = _REPO_ROOT / "docs" / "agent-lessons-learned.md"


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

    async def append_lesson(self, lesson_text: str) -> str:
        """Append *lesson_text* to the lessons file.

        Returns a short confirmation string on success, or an error description
        on failure.  Never raises — the self_learning_agent should never crash
        due to a file-write error.
        """
        if not lesson_text or not lesson_text.strip():
            return "ERROR: lesson_text is empty — nothing appended."

        # DRY_RUN guard: log but do not write.
        if os.environ.get("DRY_RUN", "").lower() == "true":
            logger.info(
                "lessons_append_dry_run",
                lesson_preview=lesson_text[:120],
            )
            return f"DRY_RUN: lesson not written to disk ({len(lesson_text)} chars logged)."

        try:
            if not LESSONS_FILE.exists():
                logger.error(
                    "lessons_file_missing_on_append",
                    path=str(LESSONS_FILE),
                )
                return f"ERROR: lessons file not found at {LESSONS_FILE}. Cannot append."

            existing = LESSONS_FILE.read_text(encoding="utf-8")
            # Always separate the new lesson from the previous content with two
            # blank lines, regardless of how the caller formatted lesson_text.
            separator = "\n\n" if existing.endswith("\n") else "\n\n\n"
            updated = existing + separator + lesson_text.strip() + "\n"
            LESSONS_FILE.write_text(updated, encoding="utf-8")

            logger.info(
                "lessons_appended",
                path=str(LESSONS_FILE),
                lesson_preview=lesson_text[:120],
                total_length=len(updated),
            )
            return (
                f"Lesson appended successfully to {LESSONS_FILE.name} "
                f"({len(lesson_text)} chars added, file now {len(updated)} chars)."
            )
        except OSError as exc:
            logger.error(
                "lessons_append_failed",
                error=str(exc),
                path=str(LESSONS_FILE),
            )
            return f"ERROR: could not write to {LESSONS_FILE}: {exc}"

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
            return await self.append_lesson(lesson_text)

        return (
            f"ERROR: unknown action '{action}'. "
            "Valid actions are 'read' and 'append'."
        )
