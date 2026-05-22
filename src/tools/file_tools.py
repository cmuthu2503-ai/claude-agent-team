"""File tools — read, write, and search-replace files with path validation.

Each tool accepts an optional ``project_root`` override on every
``execute()`` call. The default ``project_root`` set at construction
time is the PLATFORM tree (``/app`` in the backend container). When an
agent is dispatched on a per-project Request, the executor passes the
per-project working tree (e.g. ``C:/ai-projects/CrewAI/``) so the same
relative path like ``frontend/src/App.tsx`` resolves to the right tree.

Without the override, an agent task targeting CrewAI would scribble
into the platform's frontend source — that broke the platform's Vite
build on 2026-05-21 16:50 when an agent's search_replace landed in the
platform's App.tsx instead of CrewAI's.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# Lines below this threshold are too small for search_replace to be meaningfully
# safer than a full rewrite. The agent should just emit the whole file.
_MIN_LINES_FOR_SURGICAL_EDIT = 0  # we'll allow surgical edits on any size

# A unique substring is required for search_replace. If the old_string appears
# more than this many times in the file, we refuse the edit and tell the agent
# to provide a longer/more specific match. Prevents accidental mass-replacement.
_MAX_MATCHES_FOR_UNIQUE_EDIT = 1

# Hard cap on the autoformatter — generous (most files reformat in <2s) but
# short enough that a hung ruff binary can't wedge a write call.
_RUFF_FORMAT_TIMEOUT_S = 15


async def _maybe_ruff_format(path: Path, content: str) -> tuple[str, bool]:
    """If `path` is a .py file and `ruff` is on PATH, pipe `content` through
    two ruff passes and return the cleaned text. SOFT-FAIL: any error
    (ruff missing, parse error, timeout) returns the original content with
    `was_formatted=False` so the write never fails because of formatting.

    Two passes (both stdin → stdout, no on-disk writes):

      1. ``ruff check --fix --select F401,F811,I001 -`` — strips the
         AUTO-FIXABLE [*] violations that ``ruff format`` does NOT
         touch. Specifically:
           • F401 — unused imports (the one that killed REQ-A6A4DB
             after 3 cycles — agent emitted ``import re`` + ``from
             pydantic import ValidationError`` without using them, and
             the rework prompt couldn't talk it into removing them).
           • F811 — redefined unused names.
           • I001 — unsorted/unformatted import block.
         These are all SEMANTICALLY SAFE: removing an unused import or
         sorting one doesn't change runtime behaviour. We do NOT pass
         a generic ``--fix`` — that would also apply non-safe fixes.

      2. ``ruff format -`` — reflows whitespace and quoting to the
         project's `line-length` setting. Knocks out E501 (line too
         long) which was the original motivation for this function.

    Why both: ``ruff format`` is purely a formatter — it does not touch
    semantics, even for trivially-safe ones like unused imports.
    Adding the ``check --fix`` pass closes the gap so the commit-gate
    never sees these classes of error.

    Returns:
        (content, was_formatted). When was_formatted is False, content is
        the original unchanged input. True when EITHER pass changed
        something — the caller doesn't care which.
    """
    if path.suffix != ".py":
        return content, False
    try:
        any_change = False
        cwd = str(path.parent)  # so ruff finds the nearest pyproject.toml

        # ── Pass 1: ruff check --fix for safe categories ──
        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff", "check",
                "--fix",
                "--select", "F401,F811,I001",
                "--exit-zero",  # don't error on remaining (non-fixable) lints
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(input=content.encode("utf-8")),
                timeout=_RUFF_FORMAT_TIMEOUT_S,
            )
            if proc.returncode == 0 and stdout:
                fixed = stdout.decode("utf-8")
                if fixed and fixed != content:
                    content = fixed
                    any_change = True
        except asyncio.TimeoutError:
            # Lint-fix pass hung — kill it and fall through to format.
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("ruff_check_fix_timeout", path=str(path))
            # Don't bail — still try the format pass below.

        # ── Pass 2: ruff format ──
        proc = await asyncio.create_subprocess_exec(
            "ruff", "format", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(input=content.encode("utf-8")),
                timeout=_RUFF_FORMAT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("ruff_format_timeout", path=str(path))
            return (content, any_change)
        if proc.returncode == 0 and stdout:
            formatted = stdout.decode("utf-8")
            if formatted and formatted != content:
                return formatted, True
        # Non-zero exit usually means the file isn't parseable as Python
        # (e.g. agent is mid-emission and emitted broken syntax). Don't
        # block the write — let the commit-gate's ruff CHECK surface it.
        return content, any_change
    except FileNotFoundError:
        # ruff not installed in this environment — leave content alone.
        return content, False
    except Exception as e:  # noqa: BLE001 — defensive catch-all
        logger.warning("ruff_format_failed", path=str(path), err=str(e))
        return content, False


class FileReadTool:
    """Reads file contents from the project directory."""

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = Path(project_root).resolve()

    def schema(self) -> dict[str, Any]:
        return {
            "name": "file_read",
            "description": "Read the contents of a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to project root"},
                },
                "required": ["path"],
            },
        }

    async def execute(self, params: dict, *, project_root: Path | None = None) -> str:
        try:
            path = self._resolve_path(params["path"], project_root)
        except ValueError as e:
            return f"Error: {e}"
        if not path.exists():
            return f"Error: File not found: {params['path']}"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

    def _resolve_path(
        self, relative_path: str, project_root: Path | None = None,
    ) -> Path:
        """Resolve ``relative_path`` under the EFFECTIVE project root.

        ``project_root`` (when passed by the executor) wins over
        ``self.project_root``. Path-traversal guard always uses the
        effective root, so a per-project task can't escape into the
        platform tree even with `..` shenanigans.
        """
        effective_root = (project_root.resolve() if project_root else self.project_root)
        resolved = (effective_root / relative_path).resolve()
        if not str(resolved).startswith(str(effective_root)):
            raise ValueError(f"Path escapes project root: {relative_path}")
        return resolved


class SearchReplaceTool:
    """Surgical single-occurrence edit on an existing file.

    Solves the "full-file re-emit hits ~470-line output ceiling" problem
    observed in REQ-7F2E07: when an agent needs to change one or two lines
    in a 800-line file, asking it to emit the entire file forces it past
    its own response-length limits and the file gets truncated mid-way.

    With this tool the agent emits ONLY the {old_string, new_string} pair
    and the system performs the substitution on disk. The agent's response
    stays short (just the diff), and large files stay intact.

    Safety properties:
      - old_string MUST appear exactly once in the file. Zero or multiple
        matches → error returned to the agent so it can supply a more
        specific match. Prevents accidental mass-replace.
      - Path must be inside project_root (no traversal).
      - File must exist (no file creation — use file_write or full-source
        emission for new files).
      - Atomic: read → check → write in one go; no partial state on failure.
    """

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = Path(project_root).resolve()

    def schema(self) -> dict[str, Any]:
        return {
            "name": "search_replace",
            "description": (
                "Make a surgical edit to an existing file by replacing one "
                "specific occurrence of `old_string` with `new_string`. Use "
                "this for SMALL changes to LARGE existing files instead of "
                "re-emitting the whole file — it avoids the output-length "
                "ceiling that truncates >470-line file emissions. The "
                "`old_string` must appear EXACTLY ONCE in the file (include "
                "enough surrounding context to make it unique)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to project root (file must exist)",
                    },
                    "old_string": {
                        "type": "string",
                        "description": (
                            "The exact text to replace. Must appear exactly once "
                            "in the file. Include surrounding context (full lines, "
                            "neighboring lines) to disambiguate."
                        ),
                    },
                    "new_string": {
                        "type": "string",
                        "description": (
                            "The replacement text. Use \"\" (empty string) to delete. "
                            "Use a multi-line string to insert several lines."
                        ),
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        }

    async def execute(self, params: dict, *, project_root: Path | None = None) -> str:
        path_str = params.get("path", "")
        old_string = params.get("old_string", "")
        new_string = params.get("new_string", "")

        if not path_str:
            return "Error: path is required."
        if not old_string:
            return (
                "Error: old_string is required and cannot be empty. "
                "To create a new file or write whole content, use file_write or emit a "
                "`### Full Source:` block instead."
            )

        try:
            path = self._resolve_path(path_str, project_root)
        except ValueError as e:
            return f"Error: {e}"

        if not path.exists():
            return (
                f"Error: File not found: {path_str}. "
                f"search_replace only edits existing files. Use a `### Full Source:` "
                f"block or file_write for new files."
            )

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

        # Uniqueness check — the agent must supply enough context that old_string
        # appears once. This is the safety property that makes surgical edits
        # robust against accidental mass-replacement.
        match_count = content.count(old_string)
        if match_count == 0:
            return (
                f"Error: old_string not found in {path_str}. "
                f"The exact text you provided does not appear in the file. "
                f"Re-check the file content (call file_read) and supply text that "
                f"matches exactly — including whitespace, indentation, and surrounding "
                f"context."
            )
        if match_count > _MAX_MATCHES_FOR_UNIQUE_EDIT:
            return (
                f"Error: old_string appears {match_count} times in {path_str}. "
                f"search_replace requires the old_string to be unique. "
                f"Include more surrounding context (e.g., adjacent lines or the "
                f"enclosing function/block) so the match is unambiguous."
            )

        new_content = content.replace(old_string, new_string, 1)

        # Auto-format the resulting file with ruff (only when it's .py and
        # ruff is available). Soft-fails to unformatted content otherwise.
        # Same rationale as FileWriteTool — agents shouldn't have to count
        # columns; the project's pyproject.toml line-length wins.
        new_content, was_formatted = await _maybe_ruff_format(path, new_content)

        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return f"Error writing file: {e}"

        # Report the effect concisely so the agent's next iteration knows it
        # succeeded. Line count delta helps the agent (and code reviewer) see
        # the magnitude of the change at a glance.
        old_lines = content.count("\n")
        new_lines = new_content.count("\n")
        delta = new_lines - old_lines
        sign = "+" if delta > 0 else ""
        logger.info(
            "search_replace_applied",
            path=path_str, old_len=len(old_string), new_len=len(new_string),
            line_delta=delta, auto_formatted=was_formatted,
        )
        # The success message ALSO instructs the agent to record this edit in
        # its `## Files Modified` final-output section. CodeWriter scans for
        # that section to know which on-disk-edited files to include in the
        # GitHub commit (search_replace edits the file directly, not via the
        # agent's text output, so CodeWriter wouldn't otherwise know about it).
        return (
            f"OK: {path_str} edited successfully. "
            f"Lines: {old_lines} → {new_lines} ({sign}{delta}).\n"
            f"REMINDER: include `{path_str}` in your final `## Files Modified` "
            f"section so the commit step picks it up. Without that line, your "
            f"edit will be on disk locally but NOT pushed to GitHub."
        )

    def _resolve_path(
        self, relative_path: str, project_root: Path | None = None,
    ) -> Path:
        """Resolve under the effective root — same semantics as FileReadTool."""
        effective_root = (project_root.resolve() if project_root else self.project_root)
        resolved = (effective_root / relative_path).resolve()
        if not str(resolved).startswith(str(effective_root)):
            raise ValueError(f"Path escapes project root: {relative_path}")
        return resolved


class FileWriteTool:
    """Creates or modifies files within the project directory."""

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = Path(project_root).resolve()

    def schema(self) -> dict[str, Any]:
        return {
            "name": "file_write",
            "description": "Write content to a file (creates parent directories if needed)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to project root"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        }

    async def execute(self, params: dict, *, project_root: Path | None = None) -> str:
        try:
            path = self._resolve_path(params["path"], project_root)
        except ValueError as e:
            return f"Error: {e}"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Auto-format Python content with `ruff format` before persisting.
            # Soft-fails to the original content on any error — the commit
            # gate will still surface real lint problems.
            content, was_formatted = await _maybe_ruff_format(path, params["content"])
            path.write_text(content, encoding="utf-8")
            logger.info(
                "file_written", path=str(path), auto_formatted=was_formatted,
            )
            note = " (auto-formatted with ruff)" if was_formatted else ""
            return f"File written: {params['path']}{note}"
        except Exception as e:
            return f"Error writing file: {e}"

    def _resolve_path(
        self, relative_path: str, project_root: Path | None = None,
    ) -> Path:
        """Resolve under the effective root — same semantics as FileReadTool."""
        effective_root = (project_root.resolve() if project_root else self.project_root)
        resolved = (effective_root / relative_path).resolve()
        if not str(resolved).startswith(str(effective_root)):
            raise ValueError(f"Path escapes project root: {relative_path}")
        return resolved
