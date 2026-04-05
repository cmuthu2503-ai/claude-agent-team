"""File tools — read and write files with path validation."""

import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


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
