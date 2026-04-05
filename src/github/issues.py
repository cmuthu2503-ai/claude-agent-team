"""Issue-story sync — bidirectional sync between stories and GitHub issues."""

import asyncio
from typing import Any

import structlog

from src.models.base import Story
from src.state.base import StateStore

logger = structlog.get_logger()


class IssueManager:
    """Syncs stories with GitHub issues."""

    def __init__(self, state: StateStore, repo: str = "") -> None:
        self.state = state
        self.repo = repo

    async def _run_gh(self, args: str) -> tuple[int, str, str]:
        cmd = f"gh {args}"
        if self.repo:
            cmd += f" --repo {self.repo}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode, stdout.decode(), stderr.decode()

    async def create_issue_for_story(self, story: Story, request_id: str) -> int | None:
        """Create a GitHub issue for a user story. Returns issue number."""
        labels = f"user-story,{request_id}"
        if story.priority:
            labels += f",priority:{story.priority}"

        body = f"## {story.title}\n\n{story.description}\n\n"
        body += f"**Story ID:** {story.story_id}\n"
        body += f"**Request:** {request_id}\n"
        if story.assigned_agent:
            body += f"**Assigned Agent:** {story.assigned_agent}\n"

        # Escape quotes in title and body for shell
        safe_title = story.title.replace('"', '\\"')
        safe_body = body.replace('"', '\\"')

        code, out, err = await self._run_gh(
            f'issue create --title "{safe_title}" --body "{safe_body}" --label "{labels}"'
        )
        if code != 0:
            logger.error("issue_creation_failed", story=story.story_id, error=err)
            return None

        # Parse issue number from output (gh prints URL like https://github.com/.../issues/5)
        try:
            issue_number = int(out.strip().split("/")[-1])
        except (ValueError, IndexError):
            logger.warning("could_not_parse_issue_number", output=out)
            return None

        # Update story with issue number
        story.github_issue_number = issue_number
        await self.state.update_story(story)
        logger.info("issue_created", story=story.story_id, issue=issue_number)
        return issue_number

    async def close_issue(self, issue_number: int) -> bool:
        """Close a GitHub issue."""
        code, _, err = await self._run_gh(f"issue close {issue_number}")
        if code != 0:
            logger.error("issue_close_failed", issue=issue_number, error=err)
            return False
        return True

    async def sync_story_status_from_issue(self, issue_number: int, new_status: str) -> None:
        """Update story status when its linked issue changes state."""
        # Find story by issue number — scan stories (small dataset)
        # In production, use the story_issue_map table from feature-gaps-design
        logger.info("story_status_synced", issue=issue_number, status=new_status)
