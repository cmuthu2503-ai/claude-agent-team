"""Confluence Cloud integration — space + page management.

Converts markdown to Confluence storage format (XHTML) before publishing,
using Python's ``markdown`` library for the conversion.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class ConfluencePushResult:
    """Returned by publish operations. Mirrors HostWriteResult shape."""
    def __init__(self, ok, page_id=None, page_url=None, action=None, error=None, skipped_reason=None):
        self.ok = ok
        self.page_id = page_id
        self.page_url = page_url
        self.action = action
        self.error = error
        self.skipped_reason = skipped_reason

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "page_id": self.page_id,
            "page_url": self.page_url,
            "action": self.action,
            "error": self.error,
            "skipped_reason": self.skipped_reason,
        }


def _md_to_storage_format(md_content: str) -> str:
    """Convert markdown to Confluence Storage Format (XHTML).

    Confluence's wiki representation is NOT markdown — it's a legacy
    wiki markup. Python's ``markdown`` library converts markdown to
    clean HTML, which we wrap in the storage-format envelope that
    Confluence's REST API accepts with ``representation=\"storage\"``.
    """
    from markdown import markdown

    html_body = markdown(
        md_content,
        extensions=["tables", "fenced_code", "codehilite", "toc", "nl2br"],
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE ac:confluence SYSTEM "confluence.dtd">'
        f"<ac:confluence>{html_body}</ac:confluence>"
    )


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
        """Create or retrieve a Confluence space."""
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
                pass
            try:
                result = client.create_space(
                    space_key=space_key,
                    space_name=space_name,
                )
                return {
                    "ok": True, "space_key": space_key,
                    "space_id": result.get("id") if result else None,
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
        """Create or update a Confluence page.

        Converts markdown to Confluence Storage Format (XHTML) before
        publishing, so the content renders correctly in Confluence."""
        def _run() -> ConfluencePushResult:
            client = self._get_client()
            body = _md_to_storage_format(content_md)

            if existing_page_id:
                try:
                    client.update_page(
                        page_id=existing_page_id,
                        title=title,
                        body=body,
                        representation="storage",
                        minor_edit=False,
                    )
                    return ConfluencePushResult(
                        ok=True,
                        page_id=existing_page_id,
                        page_url=f"{self._url}/spaces/{space_key}/pages/{existing_page_id}",
                        action="updated",
                    )
                except Exception as e:
                    logger.warning("confluence.update_page_failed page_id=%s error=%s", existing_page_id, e)
                    return ConfluencePushResult(
                        ok=False, action="updated",
                        error=f"update_page: {e}",
                    )

            try:
                result = client.create_page(
                    space=space_key,
                    title=title,
                    body=body,
                    representation="storage",
                )
                page_id = result.get("id") if result else None
                if not page_id:
                    page_id = f"{space_key}-{title.replace(' ', '_')}"
                return ConfluencePushResult(
                    ok=True,
                    page_id=page_id,
                    page_url=f"{self._url}/spaces/{space_key}/pages/{page_id}",
                    action="created",
                )
            except Exception as e:
                logger.warning("confluence.create_page_failed space=%s title=%s error=%s", space_key, title, e)
                return ConfluencePushResult(
                    ok=False, action="created",
                    error=f"create_page: {e}",
                )

        return await asyncio.to_thread(_run)


def create_confluence_client(config: dict | None = None) -> ConfluenceCloudClient | None:
    """Build a ConfluenceCloudClient from environment + config."""
    import os

    enabled = os.getenv("CONFLUENCE_ENABLED", "").strip().lower()
    if enabled != "true":
        return None

    url = os.getenv("CONFLUENCE_URL", "").strip()
    email = os.getenv("CONFLUENCE_EMAIL", "").strip()
    token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()

    if config:
        cf = config.get("integrations", {}).get("confluence", {})
        if not url:
            url = cf.get("url", "")
        if not email:
            email = cf.get("email", "")

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
