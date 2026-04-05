"""Repository initialization and management."""

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class RepoManager:
    """Creates and configures GitHub repositories."""

    def __init__(self, org: str, default_branch: str = "main") -> None:
        self.org = org
        self.default_branch = default_branch

    async def _run_gh(self, args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_shell(
            f"gh {args}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode, stdout.decode(), stderr.decode()

    async def create_repo(self, repo_name: str, description: str = "") -> dict[str, Any]:
        """Create a new GitHub repo with standard configuration."""
        full_name = f"{self.org}/{repo_name}"
        code, out, err = await self._run_gh(
            f'repo create {full_name} --public --description "{description}" --clone=false'
        )
        if code != 0:
            if "already exists" in err.lower():
                logger.info("repo_already_exists", repo=full_name)
                return {"repo": full_name, "created": False, "message": "Already exists"}
            return {"repo": full_name, "created": False, "error": err}

        logger.info("repo_created", repo=full_name)
        return {"repo": full_name, "created": True}

    async def setup_branch_protection(self, repo_name: str) -> dict[str, Any]:
        """Configure branch protection on main."""
        full_name = f"{self.org}/{repo_name}"
        code, out, err = await self._run_gh(
            f"api repos/{full_name}/branches/{self.default_branch}/protection "
            f'--method PUT --input - <<< \'{{"required_status_checks":{{"strict":true,"contexts":["test-backend","lint-python"]}},'
            f'"enforce_admins":false,"required_pull_request_reviews":{{"required_approving_review_count":1}},'
            f'"restrictions":null}}\''
        )
        if code != 0:
            logger.warning("branch_protection_failed", repo=full_name, error=err)
            return {"protected": False, "error": err}

        logger.info("branch_protection_set", repo=full_name, branch=self.default_branch)
        return {"protected": True, "branch": self.default_branch}

    async def repo_exists(self, repo_name: str) -> bool:
        full_name = f"{self.org}/{repo_name}"
        code, _, _ = await self._run_gh(f"repo view {full_name} --json name")
        return code == 0
