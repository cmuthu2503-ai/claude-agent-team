"""File tools — read, write, and search-replace files with path validation."""

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

    async def execute(self, params: dict) -> str:
        path = self._resolve_path(params["path"])
        if not path.exists():
            return f"Error: File not found: {params['path']}"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

    def _resolve_path(self, relative_path: str) -> Path:
        resolved = (self.project_root / relative_path).resolve()
        if not str(resolved).startswith(str(self.project_root)):
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

    async def execute(self, params: dict) -> str:
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
            path = self._resolve_path(path_str)
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
            line_delta=delta,
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

    def _resolve_path(self, relative_path: str) -> Path:
        resolved = (self.project_root / relative_path).resolve()
        if not str(resolved).startswith(str(self.project_root)):
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

    async def execute(self, params: dict) -> str:
        path = self._resolve_path(params["path"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params["content"], encoding="utf-8")
            logger.info("file_written", path=str(path))
            return f"File written: {params['path']}"
        except Exception as e:
            return f"Error writing file: {e}"

    def _resolve_path(self, relative_path: str) -> Path:
        resolved = (self.project_root / relative_path).resolve()
        if not str(resolved).startswith(str(self.project_root)):
            raise ValueError(f"Path escapes project root: {relative_path}")
        return resolved
