"""Confluence Cloud integration — space + page management.

Uses ``atlassian-python-api`` under the hood. All external calls are wrapped
in ``asyncio.to_thread()`` to avoid blocking the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ConfluencePushResult:
    """Returned by publish operations. Mirrors HostWriteResult shape."""
    ok: bool
    page_id: str | None = None
    page_url: str | None = None
    action: str | None = None    # 'created', 'updated', 'skipped'
    error: str | None = None
    skipped_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "page_id": self.page_id,
            "page_url": self.page_url,
            "action": self.action,
            "error": self.error,
            "skipped_reason": self.skipped_reason,
        }


class ConfluenceCloudClient:
    """Async-safe wrapper around atlassian.Confluence."""

    def __init__(self, url: str, email: str, api_token: str, timeout: int = 30) -> None:
        self._url = url.rstrip("/")
        self._email = email
        self._token = api_token
        self._timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            from atlassian import Confluence
            self._client = Confluence(
                url=self._url,
                username=self._email,
                password=self._token,
                timeout=self._timeout,
            )
        return self._client

    async def ensure_space(self, space_key: str, space_name: str) -> dict:
        """Create or retrieve a Confluence space.

        Returns:
            {ok: bool, space_key: str, space_id: str | None,
             space_url: str | None, created: bool, error: str | None}
        """
        def _run():
            client = self._get_client()
            try:
                existing = client.get_space(space_key, expand="")
                if existing:
                    return {
                        "ok": True, "space_key": space_key,
                        "space_id": existing.get("id"),
                        "space_url": f"{self._url}/spaces/{space_key}",
                        "created": False,
                    }
            except Exception:
                pass  # Space doesn't exist — create it

            try:
                result = client.create_space(
                    space_key=space_key,
                    space_name=space_name,
                )
                return {
                    "ok": True, "space_key": space_key,
                    "space_id": result.get("id"),
                    "space_url": f"{self._url}/spaces/{space_key}",
                    "created": True,
                }
            except Exception as e:
                logger.warning("confluence.create_space_failed space_key=%s error=%s", space_key, e)
                return {"ok": False, "space_key": space_key, "error": str(e)}

        return await asyncio.to_thread(_run)

    async def upsert_page(
        self,
        space_key: str,
        title: str,
        content_md: str,
        existing_page_id: str | None = None,
    ) -> ConfluencePushResult:
        """Create or update a Confluence page with markdown content.

        Args:
            space_key: Confluence space key (e.g., 'PROJ').
            title: Page title (e.g., 'PRD v1').
            content_md: Markdown content to publish.
            existing_page_id: If set, update this page instead of creating.
        """
        def _run() -> ConfluencePushResult:
            client = self._get_client()

            if existing_page_id:
                try:
                    client.update_page(
                        page_id=existing_page_id,
                        title=title,
                        body=content_md,
                        representation="markdown",
                        minor_edit=False,
                    )
                    return ConfluencePushResult(
                        ok=True,
                        page_id=existing_page_id,
                        page_url=f"{self._url}/spaces/{space_key}/pages/{existing_page_id}",
                        action="updated",
                    )
                except Exception as e:
                    logger.warning(
                        "confluence.update_page_failed page_id=%s error=%s",
                        existing_page_id, e,
                    )
                    return ConfluencePushResult(
                        ok=False, action="updated",
                        error=f"update_page: {e}",
                    )

            # Create new page
            try:
                result = client.create_page(
                    space=space_key,
                    title=title,
                    body=content_md,
                    representation="markdown",
                )
                page_id = result.get("id")
                return ConfluencePushResult(
                    ok=True,
                    page_id=page_id,
                    page_url=f"{self._url}/spaces/{space_key}/pages/{page_id}",
                    action="created",
                )
            except Exception as e:
                logger.warning(
                    "confluence.create_page_failed space=%s title=%s error=%s",
                    space_key, title, e,
                )
                return ConfluencePushResult(
                    ok=False, action="created",
                    error=f"create_page: {e}",
                )

        return await asyncio.to_thread(_run)


def create_confluence_client(config: dict | None = None) -> ConfluenceCloudClient | None:
    """Build a ConfluenceCloudClient from environment + config.

    Returns None when CONFLUENCE_ENABLED is not 'true' or credentials are missing.
    """
    import os

    enabled = os.getenv("CONFLUENCE_ENABLED", "").strip().lower()
    if enabled != "true":
        return None

    url = os.getenv("CONFLUENCE_URL", "").strip()
    email = os.getenv("CONFLUENCE_EMAIL", "").strip()
    token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()

    # Fall back to config file
    if config:
        cf = config.get("integrations", {}).get("confluence", {})
        if not url:
            url = cf.get("url", "")
        if not email:
            email = cf.get("email", "")

    # Try Docker secret for token
    if not token:
        try:
            from src.utils.secrets import read_secret
            token = read_secret("confluence_api_token", "CONFLUENCE_API_TOKEN", "")
        except Exception:
            pass

    if not url or not email or not token:
        logger.info("confluence_skipped", reason="missing credentials")
        return None

    return ConfluenceCloudClient(url=url, email=email, api_token=token)
