"""Code tools — sandboxed execution, test runner, code analysis."""

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class CodeExecTool:
    """Execute code in a sandboxed subprocess."""

    def __init__(self, working_dir: str = ".") -> None:
        self.working_dir = working_dir

    def schema(self) -> dict[str, Any]:
        return {
            "name": "code_exec",
            "description": "Execute a shell command (for running scripts, builds, etc.)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 60},
                },
                "required": ["command"],
            },
        }

    async def execute(self, params: dict) -> str:
        command = params["command"]
        timeout = min(params.get("timeout", 60), 120)  # cap at 2 min

        # Block dangerous commands
        blocked = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd"]
        for pattern in blocked:
            if pattern in command:
                return f"Error: Blocked dangerous command pattern: {pattern}"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode() if stdout else ""
            errors = stderr.decode() if stderr else ""
            result = output
            if errors:
                result += f"\nSTDERR:\n{errors}"
            if proc.returncode != 0:
                result = f"Exit code {proc.returncode}:\n{result}"
            return result[:10000]  # truncate large outputs
        except asyncio.TimeoutError:
            return f"Error: Command timed out after {timeout} seconds"
        except Exception as e:
            return f"Error: {e}"


class TestRunnerTool:
    """Run test suites (pytest for Python, jest/vitest for JS)."""

    def __init__(self, working_dir: str = ".") -> None:
        self.working_dir = working_dir

    def schema(self) -> dict[str, Any]:
        return {
            "name": "test_runner",
            "description": "Run test suite. Specify path for targeted runs.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "framework": {"type": "string", "enum": ["pytest", "jest", "vitest"], "default": "pytest"},
                    "path": {"type": "string", "description": "Test file or directory", "default": "tests/"},
                    "args": {"type": "string", "description": "Additional arguments", "default": ""},
                },
            },
        }

    async def execute(self, params: dict) -> str:
        framework = params.get("framework", "pytest")
        path = params.get("path", "tests/")
        args = params.get("args", "")

        commands = {
            "pytest": f"python -m pytest {path} {args} --tb=short -q",
            "jest": f"npx jest {path} {args}",
            "vitest": f"npx vitest run {path} {args}",
        }
        cmd = commands.get(framework, f"python -m pytest {path}")
        exec_tool = CodeExecTool(self.working_dir)
        return await exec_tool.execute({"command": cmd, "timeout": 120})


class CodeAnalysisTool:
    """Run static analysis and linting."""

    def __init__(self, working_dir: str = ".") -> None:
        self.working_dir = working_dir

    def schema(self) -> dict[str, Any]:
        return {
            "name": "code_analysis",
            "description": "Run linting and static analysis on code",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": ["ruff", "mypy", "eslint"], "default": "ruff"},
                    "path": {"type": "string", "description": "File or directory to analyze", "default": "src/"},
                },
            },
        }

    async def execute(self, params: dict) -> str:
        tool = params.get("tool", "ruff")
        path = params.get("path", "src/")
        commands = {
            "ruff": f"ruff check {path}",
            "mypy": f"mypy {path}",
            "eslint": f"npx eslint {path}",
        }
        cmd = commands.get(tool, f"ruff check {path}")
        exec_tool = CodeExecTool(self.working_dir)
        return await exec_tool.execute({"command": cmd, "timeout": 60})
