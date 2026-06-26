"""JIRA Cloud integration — Epic, Story, Sub-task management + status sync.

Uses ``atlassian-python-api`` v4.x under the hood. All external calls are
wrapped in ``asyncio.to_thread()`` to avoid blocking the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class JiraPushResult:
    """Returned by JIRA issue operations. Mirrors HostWriteResult shape."""
    ok: bool
    issue_key: str | None = None
    issue_url: str | None = None
    action: str | None = None    # 'created', 'updated', 'skipped', 'transitioned'
    error: str | None = None
    skipped_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "issue_key": self.issue_key,
            "issue_url": self.issue_url,
            "action": self.action,
            "error": self.error,
            "skipped_reason": self.skipped_reason,
        }


class JiraCloudClient:
    """Async-safe wrapper around atlassian.Jira (v4.x)."""

    def __init__(self, url: str, email: str, api_token: str, timeout: int = 30) -> None:
        self._url = url.rstrip("/")
        self._email = email
        self._token = api_token
        self._timeout = timeout
        self._client = None
        self._account_id: str | None = None  # cached on first use
        self._epic_link_field_id: str | None = None

    def _get_client(self):
        if self._client is None:
            from atlassian import Jira
            self._client = Jira(
                url=self._url,
                username=self._email,
                password=self._token,
                timeout=self._timeout,
            )
        return self._client

    async def _get_account_id(self) -> str:
        """Get the Atlassian account ID for the authenticated user."""
        if self._account_id:
            return self._account_id
        def _run():
            client = self._get_client()
            me = client.myself()
            return me.get("accountId", "")
        self._account_id = await asyncio.to_thread(_run)
        return self._account_id

    async def _discover_epic_link_id(self) -> str:
        """Discover the Epic Link custom field ID for this JIRA instance."""
        if self._epic_link_field_id:
            return self._epic_link_field_id
        def _run():
            client = self._get_client()
            fields = client.get_all_fields()
            for f in fields:
                if f.get("name", "").lower() == "epic link":
                    return f["id"]
            logger.warning("epic_link_field_not_found; falling back to customfield_10014")
            return "customfield_10014"
        self._epic_link_field_id = await asyncio.to_thread(_run)
        return self._epic_link_field_id

    # ── Project creation ──────────────────────────────────────

    async def create_project(self, key: str, name: str) -> JiraPushResult:
        """Auto-create a JIRA project via the REST API v3 raw JSON method.

        ``atlassian-python-api`` v4.x dropped ``create_project()``; we use
        ``create_project_from_raw_json`` with the standard Scrum template body.
        """
        def _run() -> JiraPushResult:
            client = self._get_client()
            body = {
                "key": key,
                "name": name,
                "projectTypeKey": "software",
                "templateKey": "com.pyxis.greenhopper.jira:gh-simplified-scrum-classic",
                "leadAccountId": "",  # will be auto-assigned for single-user instances
            }
            try:
                result = client.create_project_from_raw_json(body)
                project_key = result.get("key", key)
                return JiraPushResult(
                    ok=True, issue_key=project_key,
                    issue_url=f"{self._url}/projects/{project_key}",
                    action="created_project",
                )
            except Exception as e:
                err = str(e)
                if "already exists" in err.lower() or "duplicate" in err.lower():
                    return JiraPushResult(
                        ok=True, issue_key=key,
                        issue_url=f"{self._url}/projects/{key}",
                        action="existing_project",
                    )
                logger.warning("jira.create_project_failed key=%s error=%s", key, e)
                return JiraPushResult(ok=False, action="created_project", error=err)

        return await asyncio.to_thread(_run)

    # ── Epic ──────────────────────────────────────────────────

    async def upsert_epic(
        self,
        project_key: str,
        title: str,
        description: str,
        existing_key: str | None = None,
    ) -> JiraPushResult:
        """Create or update a JIRA Epic issue."""
        def _run() -> JiraPushResult:
            client = self._get_client()
            fields = {
                "project": {"key": project_key},
                "summary": title[:255],
                "description": description[:32767],
                "issuetype": {"name": "Epic"},
            }
            fields["customfield_10011"] = title[:255]

            if existing_key:
                try:
                    client.update_issue(existing_key, update={
                        "summary": [{"set": title[:255]}],
                        "description": [{"set": description[:32767]}],
                    })
                    return JiraPushResult(
                        ok=True, issue_key=existing_key,
                        issue_url=f"{self._url}/browse/{existing_key}",
                        action="updated",
                    )
                except Exception as e:
                    logger.warning("jira.update_epic_failed key=%s error=%s", existing_key, e)
                    return JiraPushResult(ok=False, action="updated", error=str(e))

            try:
                result = client.create_issue(fields=fields)
                key = result.get("key")
                return JiraPushResult(
                    ok=True, issue_key=key,
                    issue_url=f"{self._url}/browse/{key}",
                    action="created",
                )
            except Exception as e:
                logger.warning("jira.create_epic_failed project=%s title=%s error=%s", project_key, title, e)
                return JiraPushResult(ok=False, action="created", error=str(e))

        return await asyncio.to_thread(_run)

    # ── Story ─────────────────────────────────────────────────

    async def upsert_story(
        self,
        project_key: str,
        parent_epic_key: str,
        title: str,
        description: str,
        depends_on: list[str] | None = None,
        existing_key: str | None = None,
    ) -> JiraPushResult:
        """Create or update a JIRA Story under a parent Epic."""
        def _run() -> JiraPushResult:
            client = self._get_client()
            fields = {
                "project": {"key": project_key},
                "summary": title[:255],
                "description": description[:32767],
                "issuetype": {"name": "Story"},
            }
            try:
                epic_link_id = None
                all_fields = client.get_all_fields()
                for f in all_fields:
                    if f.get("name", "").lower() == "epic link":
                        epic_link_id = f["id"]
                        break
            except Exception:
                epic_link_id = "customfield_10014"
            if epic_link_id:
                fields[epic_link_id] = parent_epic_key

            if existing_key:
                try:
                    client.update_issue(existing_key, update={
                        "summary": [{"set": title[:255]}],
                        "description": [{"set": description[:32767]}],
                    })
                    return JiraPushResult(
                        ok=True, issue_key=existing_key,
                        issue_url=f"{self._url}/browse/{existing_key}",
                        action="updated",
                    )
                except Exception as e:
                    logger.warning("jira.update_story_failed key=%s error=%s", existing_key, e)
                    return JiraPushResult(ok=False, action="updated", error=str(e))

            try:
                result = client.create_issue(fields=fields)
                key = result.get("key")
                if depends_on:
                    for dep_key in depends_on:
                        try:
                            client.create_issue_link(data={
                                "type": {"name": "Blocks"},
                                "inwardIssue": {"key": dep_key},
                                "outwardIssue": {"key": key},
                            })
                        except Exception as link_err:
                            logger.warning("jira.link_failed key=%s dep=%s error=%s", key, dep_key, link_err)
                return JiraPushResult(
                    ok=True, issue_key=key,
                    issue_url=f"{self._url}/browse/{key}",
                    action="created",
                )
            except Exception as e:
                logger.warning("jira.create_story_failed project=%s title=%s error=%s", project_key, title, e)
                return JiraPushResult(ok=False, action="created", error=str(e))

        return await asyncio.to_thread(_run)

    # ── Sub-task ──────────────────────────────────────────────

    async def upsert_subtask(
        self,
        project_key: str,
        parent_story_key: str,
        title: str,
        description: str,
        depends_on: list[str] | None = None,
        existing_key: str | None = None,
    ) -> JiraPushResult:
        """Create or update a JIRA Sub-task under a parent Story."""
        def _run() -> JiraPushResult:
            client = self._get_client()
            fields = {
                "project": {"key": project_key},
                "summary": title[:255],
                "description": description[:32767],
                "issuetype": {"name": "Sub-task"},
                "parent": {"key": parent_story_key},
            }

            if existing_key:
                try:
                    client.update_issue(existing_key, update={
                        "summary": [{"set": title[:255]}],
                        "description": [{"set": description[:32767]}],
                    })
                    return JiraPushResult(
                        ok=True, issue_key=existing_key,
                        issue_url=f"{self._url}/browse/{existing_key}",
                        action="updated",
                    )
                except Exception as e:
                    return JiraPushResult(ok=False, action="updated", error=str(e))

            try:
                result = client.create_issue(fields=fields)
                key = result.get("key")
                if depends_on:
                    for dep_key in depends_on:
                        try:
                            client.create_issue_link(data={
                                "type": {"name": "Blocks"},
                                "inwardIssue": {"key": dep_key},
                                "outwardIssue": {"key": key},
                            })
                        except Exception:
                            pass
                return JiraPushResult(
                    ok=True, issue_key=key,
                    issue_url=f"{self._url}/browse/{key}",
                    action="created",
                )
            except Exception as e:
                logger.warning("jira.create_subtask_failed parent=%s error=%s", parent_story_key, e)
                return JiraPushResult(ok=False, action="created", error=str(e))

        return await asyncio.to_thread(_run)

    # ── Transitions ───────────────────────────────────────────

    async def transition_issue(self, issue_key: str, target_status: str) -> JiraPushResult:
        """Transition a JIRA issue by status NAME (v4.x API is name-based)."""
        def _run() -> JiraPushResult:
            client = self._get_client()
            try:
                client.issue_transition(issue_key, target_status)
                return JiraPushResult(
                    ok=True, issue_key=issue_key,
                    issue_url=f"{self._url}/browse/{issue_key}",
                    action="transitioned",
                )
            except Exception as e:
                logger.warning("jira.transition_failed key=%s target=%s error=%s", issue_key, target_status, e)
                return JiraPushResult(ok=False, issue_key=issue_key, action="transitioned", error=str(e))

        return await asyncio.to_thread(_run)


def create_jira_client(config: dict | None = None) -> JiraCloudClient | None:
    """Build a JiraCloudClient from environment + config."""
    import os

    enabled = os.getenv("JIRA_ENABLED", "").strip().lower()
    if enabled != "true":
        return None

    url = os.getenv("JIRA_URL", "").strip()
    email = os.getenv("JIRA_EMAIL", "").strip()
    token = os.getenv("JIRA_API_TOKEN", "").strip()

    if config:
        jf = config.get("integrations", {}).get("jira", {})
        if not url:
            url = jf.get("url", "")
        if not email:
            email = jf.get("email", "")

    if not token:
        try:
            from src.utils.secrets import read_secret
            token = read_secret("jira_api_token", "JIRA_API_TOKEN", "")
        except Exception:
            pass

    if not url or not email or not token:
        logger.info("jira_skipped", reason="missing credentials")
        return None

    return JiraCloudClient(url=url, email=email, api_token=token)
