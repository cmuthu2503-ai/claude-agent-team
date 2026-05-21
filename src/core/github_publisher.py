"""GitHubPublisher — atomic multi-file commits to GitHub via the Trees API.

Used by both ResearchPublisher (research artifacts → docs/research/) and
CodeWriter (deployment code → src/, frontend/, etc.).

No git CLI dependency — everything is HTTPS calls authenticated by GITHUB_TOKEN.
"""

import base64
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger()

GITHUB_API = "https://api.github.com"


class GitHubPublishError(Exception):
    """Raised when a GitHub Trees API operation fails."""


class GitHubRepoCreateError(GitHubPublishError):
    """Raised when repo creation fails. Has `status_code` so callers (the
    route layer) can translate to a specific HTTPException."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# WS-07 — Pull (owner, repo) out of a GitHub URL. Tolerant of trailing
# slashes, ".git" suffixes, and "git@github.com:owner/repo" SSH form.
# Returns (None, None) if it doesn't look like a github.com URL — caller
# decides what fallback to use.
_GH_HTTPS_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/?#.]+)", re.IGNORECASE
)
_GH_SSH_RE = re.compile(r"^git@github\.com:([^/]+)/([^/?#.]+)", re.IGNORECASE)


def extract_owner_repo(url: str | None) -> tuple[str, str] | None:
    """Return ('owner', 'repo') or None if the URL isn't a github.com link.
    Strips ".git" suffix and trailing slashes."""
    if not url:
        return None
    url = url.strip()
    for pattern in (_GH_HTTPS_RE, _GH_SSH_RE):
        m = pattern.match(url)
        if m:
            owner, repo = m.group(1), m.group(2)
            repo = repo.removesuffix(".git")
            if owner and repo:
                return owner, repo
    return None


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
        repo: str | None = None,
        branch: str | None = None,
    ) -> dict[str, str]:
        """Commit a set of files to a branch in a single atomic commit.

        Args:
            files: dict mapping repo-relative path → content. Content can be a
                   `str` (treated as UTF-8 text) or `bytes` (treated as binary).
            commit_message: full commit message (subject + body)
            repo: "owner/name" target. Defaults to constructor / env var
                  (the platform's own repo) — pass explicitly for WS-08 per-
                  project routing.
            branch: target branch. Defaults to constructor / env var.

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
        target_repo = repo or self.repo
        target_branch = branch or self.branch
        if not (self.token and not self.token.startswith("ghp_xxxxx")):
            raise GitHubPublishError(
                "GitHubPublisher not configured: set GITHUB_TOKEN"
            )
        if not target_repo:
            raise GitHubPublishError(
                "GitHubPublisher not configured: no target repo (pass repo= or set GITHUB_REPO)"
            )
        if not files:
            raise GitHubPublishError("No files to commit")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repo_path = f"{GITHUB_API}/repos/{target_repo}"
        # Save vars used below in success-log + return URL.
        self_repo_for_log = target_repo
        self_branch_for_log = target_branch

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # 1. Get current branch ref
                r = await client.get(
                    f"{repo_path}/git/refs/heads/{target_branch}", headers=headers
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
                    f"{repo_path}/git/refs/heads/{target_branch}",
                    headers=headers,
                    json={"sha": new_commit_sha, "force": False},
                )
                r.raise_for_status()

                logger.info(
                    "github_committed",
                    repo=self_repo_for_log,
                    branch=self_branch_for_log,
                    sha=new_commit_sha[:8],
                    files=len(files),
                )
                return {
                    "sha": new_commit_sha,
                    "short_sha": new_commit_sha[:8],
                    "url": f"https://github.com/{target_repo}/commit/{new_commit_sha}",
                    "parent_sha": parent_sha,
                }

            except httpx.HTTPStatusError as e:
                detail = e.response.text[:300] if e.response else str(e)
                raise GitHubPublishError(
                    f"GitHub API error {e.response.status_code if e.response else '?'}: {detail}"
                ) from e
            except httpx.HTTPError as e:
                raise GitHubPublishError(f"GitHub HTTP error: {e}") from e

    async def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = True,
        org: str | None = None,
    ) -> dict[str, str]:
        """WS-02 — create a new GitHub repo under the authenticated user
        (or under `org` if supplied, falling back to GITHUB_PROJECT_ORG env).

        Creates with `auto_init=true` so the repo starts with a README +
        a `main` branch — no "empty repo" edge case for first commit.

        Returns dict with `name`, `full_name` ("owner/repo"), `html_url`,
        `default_branch`. Raises GitHubRepoCreateError with `status_code` set
        for caller-side error mapping (403 scope, 422 collision, etc.).
        """
        if not (self.token and not self.token.startswith("ghp_xxxxx")):
            raise GitHubRepoCreateError(
                "GitHubPublisher not configured: set GITHUB_TOKEN",
                status_code=None,
            )
        org = org or os.getenv("GITHUB_PROJECT_ORG") or ""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = {
            "name": name,
            "description": description or "",
            "private": private,
            "auto_init": True,  # ensures main branch + README exist for first commit
        }
        # `POST /orgs/{org}/repos` if an org is set; otherwise `POST /user/repos`
        # creates under the authenticated user's namespace.
        url = (
            f"{GITHUB_API}/orgs/{org}/repos" if org
            else f"{GITHUB_API}/user/repos"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.post(url, headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
                logger.info(
                    "github_repo_created",
                    full_name=data.get("full_name"),
                    private=data.get("private"),
                    namespace=org or "(user)",
                )
                return {
                    "name": data.get("name", name),
                    "full_name": data.get("full_name", ""),
                    "html_url": data.get("html_url", ""),
                    "default_branch": data.get("default_branch", "main"),
                }
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response else None
                # Try to pull GitHub's structured error so we can give the user
                # something actionable rather than a JSON dump.
                detail = ""
                try:
                    body_json = e.response.json() if e.response else {}
                    if isinstance(body_json, dict):
                        detail = body_json.get("message", "") or ""
                        # 422 collisions come back as {"errors":[{"message":"..."}]}
                        errs = body_json.get("errors") or []
                        if errs and isinstance(errs, list):
                            extra = errs[0].get("message") if isinstance(errs[0], dict) else None
                            if extra:
                                detail = f"{detail} — {extra}" if detail else extra
                except Exception:
                    detail = (e.response.text[:200] if e.response else str(e))
                logger.warning(
                    "github_repo_create_failed",
                    status=status,
                    detail=detail[:200],
                    name=name,
                )
                raise GitHubRepoCreateError(
                    detail or f"GitHub API returned {status}",
                    status_code=status,
                ) from e
            except httpx.HTTPError as e:
                raise GitHubRepoCreateError(
                    f"GitHub HTTP error: {e}", status_code=None,
                ) from e

    # NOTE: repo deletion is intentionally NOT implemented here. The
    # project-delete flow used to call DELETE /repos/{owner}/{repo} but
    # it ran into per-account permission issues that varied by token —
    # the user manages GitHub repos manually via the GitHub UI. See
    # src/api/routes/projects.py::delete_project for the current scope.
