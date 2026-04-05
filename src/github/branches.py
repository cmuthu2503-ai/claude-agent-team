"""Branching strategy — umbrella feature branches with sub-branches."""

import asyncio
import re
from typing import Any

import structlog

logger = structlog.get_logger()


class BranchManager:
    """Manages the umbrella + sub-branch strategy."""

    def __init__(self, working_dir: str = ".") -> None:
        self.working_dir = working_dir

    async def _run_git(self, args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_shell(
            f"git {args}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode, stdout.decode(), stderr.decode()

    def make_branch_name(self, request_id: str, slug: str, sub_type: str | None = None) -> str:
        """Generate branch name from request ID and slug.

        Examples:
            make_branch_name("REQ-042", "login-page-jwt-auth") -> "feature/REQ-042-login-page-jwt-auth"
            make_branch_name("REQ-042", "login-page-jwt-auth", "backend") -> "feature/REQ-042-backend"
        """
        safe_slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())[:40]
        if sub_type:
            return f"feature/{request_id}-{sub_type}"
        return f"feature/{request_id}-{safe_slug}"

    async def create_umbrella_branch(self, request_id: str, slug: str, base: str = "main") -> str:
        """Create the umbrella feature branch."""
        branch_name = self.make_branch_name(request_id, slug)
        await self._run_git(f"checkout {base}")
        await self._run_git("pull origin " + base)
        code, _, err = await self._run_git(f"checkout -b {branch_name}")
        if code != 0:
            if "already exists" in err:
                await self._run_git(f"checkout {branch_name}")
                return branch_name
            logger.error("branch_creation_failed", branch=branch_name, error=err)
        else:
            await self._run_git(f"push -u origin {branch_name}")
            logger.info("umbrella_branch_created", branch=branch_name)
        return branch_name

    async def create_sub_branch(self, request_id: str, sub_type: str, umbrella: str) -> str:
        """Create a sub-branch off the umbrella branch."""
        branch_name = self.make_branch_name(request_id, "", sub_type)
        await self._run_git(f"checkout {umbrella}")
        code, _, err = await self._run_git(f"checkout -b {branch_name}")
        if code != 0 and "already exists" in err:
            await self._run_git(f"checkout {branch_name}")
        else:
            await self._run_git(f"push -u origin {branch_name}")
            logger.info("sub_branch_created", branch=branch_name, parent=umbrella)
        return branch_name

    async def cleanup_merged_branches(self, request_id: str) -> list[str]:
        """Delete all branches for a request after merge."""
        code, out, _ = await self._run_git("branch -r --merged origin/main")
        deleted = []
        for line in out.strip().split("\n"):
            branch = line.strip().replace("origin/", "")
            if branch.startswith(f"feature/{request_id}"):
                await self._run_git(f"push origin --delete {branch}")
                deleted.append(branch)
                logger.info("branch_deleted", branch=branch)
        return deleted
