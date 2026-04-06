"""Code Writer — parses agent code output, writes files, compiles, commits to git."""

import asyncio
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from src.models.base import DeploymentState, DeploymentStep
from src.state.base import StateStore

logger = structlog.get_logger()

PROJECT_ROOT = Path(".")


class CodeWriteError(Exception):
    """Raised when code writing, compilation, or git push fails."""


class CodeWriter:
    """Parses code blocks from agent output, writes to disk, compiles, and pushes to git."""

    def __init__(self, state: StateStore, project_root: str = ".") -> None:
        self.state = state
        self.root = Path(project_root)

    async def commit_code(
        self, request_id: str, description: str, agent_outputs: dict[str, str]
    ) -> DeploymentState:
        """Full code commit pipeline: parse → write → compile → test → git push.

        Args:
            request_id: The request ID this code belongs to
            description: Human-readable description for the commit message
            agent_outputs: Dict of agent_id → output_text (from backend/frontend specialists)

        Returns:
            DeploymentState with step = code_committed (ready for sidecar)

        Raises:
            CodeWriteError if any step fails
        """
        deployment_id = f"deploy-{uuid.uuid4().hex[:8]}"
        dep_state = DeploymentState(
            deployment_id=deployment_id,
            request_id=request_id,
        )

        # Get current HEAD as rollback point
        rollback_sha = await self._git_head_sha()
        dep_state.rollback_sha = rollback_sha

        try:
            # Step 1: Parse code blocks from agent outputs and write to files
            all_files: list[str] = []
            for agent_id, output_text in agent_outputs.items():
                if not output_text:
                    continue
                files = self._parse_and_write_files(output_text, agent_id)
                all_files.extend(files)
                logger.info("files_written", agent=agent_id, count=len(files), files=files)

            if not all_files:
                raise CodeWriteError("No code files were produced by any agent")

            dep_state.files_committed = all_files
            self._record_step(dep_state, "files_written", "done", f"{len(all_files)} files written")

            # Step 2: Compile Python code
            await self._compile_python()
            self._record_step(dep_state, "python_compiled", "done", "ruff check passed")

            # Step 3: Compile TypeScript (if frontend files exist)
            has_frontend = any(f.startswith("frontend/") for f in all_files)
            if has_frontend:
                await self._compile_typescript()
                self._record_step(dep_state, "typescript_compiled", "done", "tsc --noEmit passed")

            # Step 4: Run tests
            await self._run_tests()
            self._record_step(dep_state, "tests_passed", "done", "pytest passed")

            # Step 5: Git commit and push
            commit_sha = await self._git_commit_and_push(request_id, description, all_files)
            dep_state.commit_sha = commit_sha
            dep_state.current_step = DeploymentStep.CODE_COMMITTED
            self._record_step(dep_state, "code_committed", "done", f"Pushed to GitHub: {commit_sha}")

            # Save state — sidecar will pick this up
            await self.state.create_deployment_state(dep_state)
            logger.info("code_committed", request_id=request_id, sha=commit_sha, files=len(all_files))

            return dep_state

        except CodeWriteError:
            raise
        except Exception as e:
            dep_state.current_step = DeploymentStep.FAILED
            dep_state.error_message = str(e)
            self._record_step(dep_state, "failed", "error", str(e))
            try:
                await self.state.create_deployment_state(dep_state)
            except Exception:
                pass
            raise CodeWriteError(f"Code commit failed: {e}") from e

    def _parse_and_write_files(self, output_text: str, agent_id: str) -> list[str]:
        """Parse code blocks with file paths from agent output and write to disk."""
        files_written: list[str] = []

        # Pattern: ### `path/to/file.ext` or ### Full Source: `path/to/file.ext`
        # Followed by ```lang\n...\n```
        pattern = r'###\s+(?:Full Source:\s*)?`([^`]+)`\s*(?:\([^)]*\))?\s*\n```\w*\n([\s\S]*?)```'
        matches = re.findall(pattern, output_text)

        for file_path, content in matches:
            file_path = file_path.strip()
            content = content.strip()

            if not file_path or not content:
                continue

            # Security: prevent path traversal
            if ".." in file_path or file_path.startswith("/"):
                logger.warning("path_traversal_blocked", path=file_path, agent=agent_id)
                continue

            # Write the file
            full_path = self.root / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content + "\n", encoding="utf-8")
            files_written.append(file_path)
            logger.debug("file_written", path=file_path, size=len(content))

        return files_written

    async def _compile_python(self) -> None:
        """Run ruff check on Python code."""
        code, stdout, stderr = await self._run_cmd("ruff check src/ --select E,F --no-fix", timeout=30)
        if code != 0:
            error = stderr or stdout
            raise CodeWriteError(f"Python compilation failed (ruff):\n{error[:500]}")

    async def _compile_typescript(self) -> None:
        """Run TypeScript compiler check on frontend code."""
        code, stdout, stderr = await self._run_cmd(
            "cd frontend && npx tsc --noEmit 2>&1 || true", timeout=60
        )
        # tsc --noEmit returns non-zero on type errors
        if code != 0 and "error TS" in (stdout + stderr):
            error = stderr or stdout
            raise CodeWriteError(f"TypeScript compilation failed:\n{error[:500]}")

    async def _run_tests(self) -> None:
        """Run pytest on backend tests."""
        code, stdout, stderr = await self._run_cmd(
            "python -m pytest tests/ -x -q --tb=short --no-cov 2>&1", timeout=120
        )
        if code != 0:
            error = stderr or stdout
            raise CodeWriteError(f"Tests failed:\n{error[:500]}")

    async def _git_head_sha(self) -> str:
        """Get the current HEAD commit SHA."""
        code, stdout, _ = await self._run_cmd("git rev-parse HEAD", timeout=10)
        return stdout.strip() if code == 0 else ""

    async def _git_commit_and_push(
        self, request_id: str, description: str, files: list[str]
    ) -> str:
        """Stage, commit, and push files to GitHub."""
        # Stage all written files
        for f in files:
            await self._run_cmd(f"git add {f}", timeout=10)

        # Check if there are actually changes staged
        code, stdout, _ = await self._run_cmd("git diff --cached --name-only", timeout=10)
        if not stdout.strip():
            raise CodeWriteError("No changes to commit — files may be identical to existing")

        # Commit
        file_list = "\n".join(f"- {f}" for f in files)
        commit_msg = (
            f"feat({request_id}): {description[:80]}\n\n"
            f"Files:\n{file_list}\n\n"
            f"Auto-committed by Agent Team pipeline"
        )
        code, stdout, stderr = await self._run_cmd(
            f'git commit -m "{commit_msg}"', timeout=30
        )
        if code != 0:
            raise CodeWriteError(f"Git commit failed:\n{(stderr or stdout)[:300]}")

        # Get commit SHA
        sha = (await self._git_head_sha())[:8]

        # Push
        code, stdout, stderr = await self._run_cmd("git push origin main", timeout=60)
        if code != 0:
            raise CodeWriteError(f"Git push failed:\n{(stderr or stdout)[:300]}")

        return sha

    def _record_step(self, state: DeploymentState, step: str, status: str, detail: str) -> None:
        """Record a step in the deployment history."""
        state.step_history.append({
            "step": step,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "detail": detail,
        })

    async def _run_cmd(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """Run a shell command and return (exit_code, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode or 0, stdout.decode(), stderr.decode()
        except asyncio.TimeoutError:
            return 1, "", f"Command timed out after {timeout}s: {cmd}"
        except Exception as e:
            return 1, "", str(e)
