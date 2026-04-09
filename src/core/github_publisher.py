"""GitHubPublisher — atomic multi-file commits to GitHub via the Trees API.

Used by both ResearchPublisher (research artifacts → docs/research/) and
CodeWriter (deployment code → src/, frontend/, etc.).

No git CLI dependency — everything is HTTPS calls authenticated by GITHUB_TOKEN.
"""

import base64
import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

GITHUB_API = "https://api.github.com"


class GitHubPublishError(Exception):
    """Raised when a GitHub Trees API operation fails."""


class GitHubPublisher:
    """Commits a set of files to GitHub atomically using the Trees API.

    Steps the API performs (one logical commit, multiple HTTP calls):
      1. GET refs/heads/<branch>           → current commit SHA
      2. GET commits/<sha>                  → base tree SHA
      3. POST blobs (one per file)          → blob SHAs
      4. POST trees (with base_tree)        → new tree SHA
      5. POST commits                       → new commit SHA
      6. PATCH refs/heads/<branch>          → fast-forward to new commit
    """

    def __init__(
        self,
        token: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
    ) -> None:
        # Token is a secret (read from /run/secrets first); repo + branch are config
        from src.utils.secrets import read_secret
        self.token = token or read_secret("github_token", "GITHUB_TOKEN")
        self.repo = repo or os.getenv("GITHUB_REPO", "")
        self.branch = branch or os.getenv("GITHUB_BRANCH", "main")

    def is_configured(self) -> bool:
        """True if a usable token and repo are present."""
        return bool(self.token) and not self.token.startswith("ghp_xxxxx") and bool(self.repo)

    async def commit_files(
        self,
        files: dict[str, bytes | str],
        commit_message: str,
    ) -> dict[str, str]:
        """Commit a set of files to the configured branch in a single atomic commit.

        Args:
            files: dict mapping repo-relative path → content. Content can be a
                   `str` (treated as UTF-8 text) or `bytes` (treated as binary).
            commit_message: full commit message (subject + body)

        Returns:
            dict with keys:
              - sha:        full new commit SHA
              - short_sha:  first 8 chars
              - url:        GitHub commit URL
              - parent_sha: SHA the new commit was branched from (rollback point)

        Raises:
            GitHubPublishError if any API call fails or the publisher is not
            configured.
        """
        if not self.is_configured():
            raise GitHubPublishError(
                "GitHubPublisher not configured: set GITHUB_TOKEN and GITHUB_REPO env vars"
            )
        if not files:
            raise GitHubPublishError("No files to commit")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repo_path = f"{GITHUB_API}/repos/{self.repo}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # 1. Get current branch ref
                r = await client.get(
                    f"{repo_path}/git/refs/heads/{self.branch}", headers=headers
                )
                r.raise_for_status()
                parent_sha = r.json()["object"]["sha"]

                # 2. Get base tree SHA
                r = await client.get(
                    f"{repo_path}/git/commits/{parent_sha}", headers=headers
                )
                r.raise_for_status()
                base_tree_sha = r.json()["tree"]["sha"]

                # 3. Create blobs (one per file)
                tree_entries: list[dict[str, Any]] = []
                for path, content in files.items():
                    if isinstance(content, str):
                        body = {"content": content, "encoding": "utf-8"}
                    else:
                        body = {
                            "content": base64.b64encode(content).decode("ascii"),
                            "encoding": "base64",
                        }
                    r = await client.post(
                        f"{repo_path}/git/blobs", headers=headers, json=body
                    )
                    r.raise_for_status()
                    tree_entries.append({
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": r.json()["sha"],
                    })

                # 4. Create tree
                r = await client.post(
                    f"{repo_path}/git/trees",
                    headers=headers,
                    json={"base_tree": base_tree_sha, "tree": tree_entries},
                )
                r.raise_for_status()
                new_tree_sha = r.json()["sha"]

                # 5. Create commit
                r = await client.post(
                    f"{repo_path}/git/commits",
                    headers=headers,
                    json={
                        "message": commit_message,
                        "tree": new_tree_sha,
                        "parents": [parent_sha],
                    },
                )
                r.raise_for_status()
                new_commit_sha = r.json()["sha"]

                # 6. Fast-forward branch
                r = await client.patch(
                    f"{repo_path}/git/refs/heads/{self.branch}",
                    headers=headers,
                    json={"sha": new_commit_sha, "force": False},
                )
                r.raise_for_status()

                logger.info(
                    "github_committed",
                    repo=self.repo,
                    branch=self.branch,
                    sha=new_commit_sha[:8],
                    files=len(files),
                )
                return {
                    "sha": new_commit_sha,
                    "short_sha": new_commit_sha[:8],
                    "url": f"https://github.com/{self.repo}/commit/{new_commit_sha}",
                    "parent_sha": parent_sha,
                }

            except httpx.HTTPStatusError as e:
                detail = e.response.text[:300] if e.response else str(e)
                raise GitHubPublishError(
                    f"GitHub API error {e.response.status_code if e.response else '?'}: {detail}"
                ) from e
            except httpx.HTTPError as e:
                raise GitHubPublishError(f"GitHub HTTP error: {e}") from e
