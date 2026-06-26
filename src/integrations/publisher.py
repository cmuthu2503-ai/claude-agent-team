"""IntegrationPublisher — orchestrates Confluence + JIRA pushes during finalize.

Follows the existing soft-fail pattern: integration failures are logged and
reported in structured result dicts but NEVER raised as exceptions. The
platform's finalize transaction is not rolled back for external failures.

JIRA projects are auto-created per platform project on first Epic finalize
— no static JIRA_PROJECT_KEY env var needed. Each platform project gets its
own isolated JIRA project (derived from the project slug), matching the
Confluence space-per-project pattern.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from slugify import slugify

from src.state.sqlite_store import SQLiteStateStore
from src.models.integration import IntegrationMapping
from src.integrations.confluence import ConfluenceCloudClient
from src.integrations.jira import JiraCloudClient

logger = logging.getLogger(__name__)

# Special entity_type for the per-project JIRA project key mapping.
# Stored once when the first Epic is finalized for a platform project.
_PROJECT_ENTITY = "project"


class IntegrationPublisher:
    """Orchestrates Confluence + JIRA publishing on finalize events."""

    def __init__(
        self,
        state: SQLiteStateStore,
        confluence: ConfluenceCloudClient | None = None,
        jira: JiraCloudClient | None = None,
    ) -> None:
        self._state = state
        self._confluence = confluence
        self._jira = jira

    @property
    def confluence_enabled(self) -> bool:
        return self._confluence is not None

    @property
    def jira_enabled(self) -> bool:
        return self._jira is not None

    # ── Confluence ──────────────────────────────────────────────

    async def push_prd(
        self, project_id: str, project_name: str, version: int, content: str,
    ) -> dict[str, Any]:
        """Publish a finalized PRD to Confluence."""
        if not self.confluence_enabled:
            return {"ok": False, "skipped": True, "skipped_reason": "confluence_disabled"}
        slug = slugify(project_name, separator="_")
        space_key = slug[:10].upper()
        result = await self._confluence.ensure_space(space_key, project_name)
        if not result["ok"]:
            return {"ok": False, "error": result.get("error", "space_creation_failed")}

        entity_id = f"prd_v{version}"
        existing = await self._state.get_integration_mapping(
            project_id, "prd", entity_id, "confluence",
        )
        existing_page_id = existing.external_ref if existing else None

        page_result = await self._confluence.upsert_page(
            space_key=space_key,
            title=f"PRD v{version}",
            content_md=content,
            existing_page_id=existing_page_id,
        )
        if page_result.ok:
            await self._state.upsert_integration_mapping(IntegrationMapping(
                mapping_id=f"map-{uuid.uuid4().hex[:8]}",
                project_id=project_id, entity_type="prd",
                entity_id=entity_id, integration="confluence",
                external_ref=page_result.page_id or "",
                external_url=page_result.page_url or "",
                sync_status="ok",
            ))
        return page_result.as_dict()

    async def push_api_spec(
        self, project_id: str, project_name: str, version: int, content: str,
    ) -> dict[str, Any]:
        """Publish a finalized API Spec to Confluence."""
        if not self.confluence_enabled:
            return {"ok": False, "skipped": True, "skipped_reason": "confluence_disabled"}
        slug = slugify(project_name, separator="_")
        space_key = slug[:10].upper()
        result = await self._confluence.ensure_space(space_key, project_name)
        if not result["ok"]:
            return {"ok": False, "error": result.get("error", "space_creation_failed")}

        entity_id = f"api_spec_v{version}"
        existing = await self._state.get_integration_mapping(
            project_id, "api_spec", entity_id, "confluence",
        )
        existing_page_id = existing.external_ref if existing else None

        page_result = await self._confluence.upsert_page(
            space_key=space_key,
            title=f"API Specification v{version}",
            content_md=content,
            existing_page_id=existing_page_id,
        )
        if page_result.ok:
            await self._state.upsert_integration_mapping(IntegrationMapping(
                mapping_id=f"map-{uuid.uuid4().hex[:8]}",
                project_id=project_id, entity_type="api_spec",
                entity_id=entity_id, integration="confluence",
                external_ref=page_result.page_id or "",
                external_url=page_result.page_url or "",
                sync_status="ok",
            ))
        return page_result.as_dict()

    # ── JIRA ────────────────────────────────────────────────────

    async def _resolve_jira_project_key(
        self, project_id: str, project_name: str,
    ) -> str | None:
        """Get or auto-create the JIRA project key for a platform project.

        On first call for a project: derives a key from the project slug,
        creates the JIRA project, and stores the mapping.
        Subsequent calls return the stored key.
        """
        # Check if we already have a project key stored
        existing = await self._state.get_integration_mapping(
            project_id, _PROJECT_ENTITY, project_id, "jira",
        )
        if existing and existing.external_ref:
            return existing.external_ref

        # Derive key from project slug
        slug = slugify(project_name, separator="")
        derived_key = slug[:10].upper()
        # JIRA keys must start with a letter and be uppercase alphanumeric
        derived_key = "".join(c for c in derived_key if c.isalnum()).upper()
        if not derived_key or not derived_key[0].isalpha():
            derived_key = "PROJ"

        result = await self._jira.create_project(derived_key, project_name)
        if not result.ok:
            logger.warning(
                "jira_auto_create_project_failed project=%s key=%s error=%s",
                project_id, derived_key, result.error,
            )
            return None

        jira_key = result.issue_key or derived_key
        # Store the mapping so subsequent calls reuse it
        await self._state.upsert_integration_mapping(IntegrationMapping(
            mapping_id=f"map-{uuid.uuid4().hex[:8]}",
            project_id=project_id, entity_type=_PROJECT_ENTITY,
            entity_id=project_id, integration="jira",
            external_ref=jira_key,
            external_url=f"https://cmuthu2503.atlassian.net/projects/{jira_key}",
            jira_project_key=jira_key, sync_status="ok",
        ))
        logger.info(
            "jira_project_auto_created platform_project=%s jira_key=%s slug=%s",
            project_id, jira_key, derived_key,
        )
        return jira_key

    async def push_epic(
        self, project_id: str, project_name: str, epic_id: str,
        title: str, description: str, acceptance_criteria: str,
    ) -> dict[str, Any]:
        """Create or update a JIRA Epic for a finalized platform Epic.

        Auto-creates the JIRA project on first call for a platform project."""
        if not self.jira_enabled:
            return {"ok": False, "skipped": True, "skipped_reason": "jira_disabled"}

        pk = await self._resolve_jira_project_key(project_id, project_name)
        if not pk:
            return {"ok": False, "skipped": True, "error": "jira_project_creation_failed"}

        desc = f"{description}\n\n*Acceptance Criteria:* {acceptance_criteria}"

        existing = await self._state.get_integration_mapping(
            project_id, "epic", epic_id, "jira",
        )
        existing_key = existing.external_ref if existing else None

        result = await self._jira.upsert_epic(
            project_key=pk, title=title, description=desc,
            existing_key=existing_key,
        )
        if result.ok:
            await self._state.upsert_integration_mapping(IntegrationMapping(
                mapping_id=f"map-{uuid.uuid4().hex[:8]}",
                project_id=project_id, entity_type="epic",
                entity_id=epic_id, integration="jira",
                external_ref=result.issue_key or "",
                external_url=result.issue_url or "",
                jira_project_key=pk, sync_status="ok",
            ))
        return result.as_dict()

    async def push_feature(
        self, project_id: str, feature_id: str, epic_id: str,
        title: str, description: str, acceptance_criteria: str,
        depends_on_feature_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create or update a JIRA Story for a finalized platform Feature.

        Resolves the JIRA project key from the parent epic's mapping."""
        if not self.jira_enabled:
            return {"ok": False, "skipped": True, "skipped_reason": "jira_disabled"}

        # Resolve parent JIRA Epic key AND project key from epic's mapping
        epic_mapping = await self._state.get_integration_mapping(
            project_id, "epic", epic_id, "jira",
        )
        if epic_mapping is None:
            return {"ok": False, "error": f"Parent epic {epic_id} not yet pushed to JIRA."}
        parent_epic_key = epic_mapping.external_ref
        pk = epic_mapping.jira_project_key
        if not pk:
            return {"ok": False, "error": "JIRA project key not found on parent epic mapping."}

        desc = f"{description}\n\n*Acceptance Criteria:* {acceptance_criteria}"

        dep_keys: list[str] = []
        if depends_on_feature_ids:
            for fdep_id in depends_on_feature_ids:
                fdep_map = await self._state.get_integration_mapping(
                    project_id, "feature", fdep_id, "jira",
                )
                if fdep_map:
                    dep_keys.append(fdep_map.external_ref)

        existing = await self._state.get_integration_mapping(
            project_id, "feature", feature_id, "jira",
        )
        existing_key = existing.external_ref if existing else None

        result = await self._jira.upsert_story(
            project_key=pk, parent_epic_key=parent_epic_key,
            title=title, description=desc, depends_on=dep_keys or None,
            existing_key=existing_key,
        )
        if result.ok:
            await self._state.upsert_integration_mapping(IntegrationMapping(
                mapping_id=f"map-{uuid.uuid4().hex[:8]}",
                project_id=project_id, entity_type="feature",
                entity_id=feature_id, integration="jira",
                external_ref=result.issue_key or "",
                external_url=result.issue_url or "",
                jira_project_key=pk, sync_status="ok",
            ))
        return result.as_dict()

    async def push_task(
        self, project_id: str, task_id: str, feature_id: str,
        title: str, description: str, depends_on_task_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a JIRA Sub-task when a platform task is dispatched.

        Resolves the JIRA project key from the parent feature's mapping."""
        if not self.jira_enabled:
            return {"ok": False, "skipped": True, "skipped_reason": "jira_disabled"}

        feature_map = await self._state.get_integration_mapping(
            project_id, "feature", feature_id, "jira",
        )
        if feature_map is None:
            return {"ok": False, "error": f"Parent feature {feature_id} not yet pushed to JIRA."}
        parent_story_key = feature_map.external_ref
        pk = feature_map.jira_project_key
        if not pk:
            return {"ok": False, "error": "JIRA project key not found on parent feature mapping."}

        dep_keys: list[str] = []
        if depends_on_task_ids:
            for tdep_id in depends_on_task_ids:
                tdep_map = await self._state.get_integration_mapping(
                    project_id, "task", tdep_id, "jira",
                )
                if tdep_map:
                    dep_keys.append(tdep_map.external_ref)

        existing = await self._state.get_integration_mapping(
            project_id, "task", task_id, "jira",
        )
        existing_key = existing.external_ref if existing else None

        result = await self._jira.upsert_subtask(
            project_key=pk, parent_story_key=parent_story_key,
            title=title, description=description,
            depends_on=dep_keys or None, existing_key=existing_key,
        )
        if result.ok:
            await self._state.upsert_integration_mapping(IntegrationMapping(
                mapping_id=f"map-{uuid.uuid4().hex[:8]}",
                project_id=project_id, entity_type="task",
                entity_id=task_id, integration="jira",
                external_ref=result.issue_key or "",
                external_url=result.issue_url or "",
                jira_project_key=pk, sync_status="ok",
            ))
        return result.as_dict()
