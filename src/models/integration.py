"""Integration mapping models — tracks Confluence/JIRA sync state.

One row = one external resource (Confluence page / JIRA issue) linked to
one platform entity (artifact, epic, feature, task). Used by
IntegrationPublisher for idempotent upsert decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal["project", "prd", "api_spec", "epic", "feature", "task"]
IntegrationName = Literal["confluence", "jira"]
SyncStatus = Literal["ok", "error", "pending"]


class IntegrationMapping(BaseModel):
    """One row = one external resource linked to one platform entity."""

    mapping_id: str                           # "map-<8hex>"
    project_id: str
    entity_type: EntityType
    entity_id: str                            # artifact_id, epic_id, feature_id, task_id
    integration: IntegrationName              # 'confluence' or 'jira'
    external_ref: str                         # Confluence page_id or JIRA issue key
    external_url: str = ""
    last_synced_at: datetime | None = None
    sync_status: SyncStatus = "ok"
    sync_error: str | None = None
    jira_project_key: str | None = None       # denormalized for fast lookup
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None
