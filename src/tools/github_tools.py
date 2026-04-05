"""GitHub tools — issues, PRs, reviews via gh CLI."""

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class GitHubAPITool:
    """Interact with GitHub via the gh CLI."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "github_api",
            "description": "GitHub operations: create issues, list PRs, manage labels",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create_issue", "close_issue", "list_issues", "add_label", "create_pr", "list_prs"],
                    },
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "labels": {"type": "string", "description": "Comma-separated labels"},
                    "issue_number": {"type": "integer"},
                    "base_branch": {"type": "string", "default": "main"},
                    "head_branch": {"type": "string"},
                },
                "required": ["action"],
            },
        }

    async def execute(self, params: dict) -> str:
        action = params["action"]
        handlers = {
            "create_issue": self._create_issue,
            "close_issue": self._close_issue,
            "list_issues": self._list_issues,
            "add_label": self._add_label,
            "create_pr": self._create_pr,
            "list_prs": self._list_prs,
        }
        handler = handlers.get(action)
        if not handler:
            return f"Unknown action: {action}"
        return await handler(params)

    async def _run_gh(self, args: str) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                f"gh {args}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return f"Error: {stderr.decode()}"
            return stdout.decode()
        except Exception as e:
            return f"Error: {e}"

    async def _create_issue(self, params: dict) -> str:
        title = params.get("title", "Untitled")
        body = params.get("body", "")
        labels = params.get("labels", "")
        cmd = f'issue create --title "{title}" --body "{body}"'
        if labels:
            cmd += f' --label "{labels}"'
        return await self._run_gh(cmd)

    async def _close_issue(self, params: dict) -> str:
        num = params.get("issue_number", 0)
        return await self._run_gh(f"issue close {num}")

    async def _list_issues(self, params: dict) -> str:
        return await self._run_gh("issue list --limit 20")

    async def _add_label(self, params: dict) -> str:
        num = params.get("issue_number", 0)
        labels = params.get("labels", "")
        return await self._run_gh(f'issue edit {num} --add-label "{labels}"')

    async def _create_pr(self, params: dict) -> str:
        title = params.get("title", "")
        body = params.get("body", "")
        base = params.get("base_branch", "main")
        head = params.get("head_branch", "")
        cmd = f'pr create --title "{title}" --body "{body}" --base {base}'
        if head:
            cmd += f" --head {head}"
        return await self._run_gh(cmd)

    async def _list_prs(self, params: dict) -> str:
        return await self._run_gh("pr list --limit 20")


class GitHubPRReviewTool:
    """Review PRs — post comments, approve, request changes."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "github_pr_review",
            "description": "Review a pull request: post comments, approve, or request changes",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer", "description": "PR number"},
                    "action": {"type": "string", "enum": ["view", "approve", "request_changes", "comment"]},
                    "body": {"type": "string", "description": "Review comment body"},
                },
                "required": ["pr_number", "action"],
            },
        }

    async def execute(self, params: dict) -> str:
        pr = params["pr_number"]
        action = params["action"]
        body = params.get("body", "")
        cmd_map = {
            "view": f"pr view {pr}",
            "approve": f'pr review {pr} --approve --body "{body}"',
            "request_changes": f'pr review {pr} --request-changes --body "{body}"',
            "comment": f'pr comment {pr} --body "{body}"',
        }
        cmd = cmd_map.get(action, f"pr view {pr}")
        try:
            proc = await asyncio.create_subprocess_shell(
                f"gh {cmd}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return f"Error: {stderr.decode()}"
            return stdout.decode()
        except Exception as e:
            return f"Error: {e}"
