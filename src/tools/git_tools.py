"""Git tools — branch, commit, push, status, diff via subprocess."""

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class GitTool:
    """Wraps git CLI commands for agent use."""

    def __init__(self, working_dir: str = ".") -> None:
        self.working_dir = working_dir

    def schema(self) -> dict[str, Any]:
        return {
            "name": "git_operations",
            "description": "Execute git commands: status, diff, branch, add, commit, push, checkout",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["status", "diff", "branch", "add", "commit", "push", "checkout", "log"],
                        "description": "Git command to execute",
                    },
                    "args": {
                        "type": "string",
                        "description": "Additional arguments for the command",
                        "default": "",
                    },
                },
                "required": ["command"],
            },
        }

    async def execute(self, params: dict) -> str:
        command = params["command"]
        args = params.get("args", "")
        allowed = {"status", "diff", "branch", "add", "commit", "push", "checkout", "log"}
        if command not in allowed:
            return f"Error: Command '{command}' not allowed. Use: {allowed}"

        cmd = f"git {command} {args}".strip()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode() if stdout else ""
            errors = stderr.decode() if stderr else ""
            if proc.returncode != 0:
                return f"Error (exit {proc.returncode}):\n{errors or output}"
            return output or "(no output)"
        except asyncio.TimeoutError:
            return "Error: Git command timed out after 30 seconds"
        except Exception as e:
            return f"Error executing git: {e}"
