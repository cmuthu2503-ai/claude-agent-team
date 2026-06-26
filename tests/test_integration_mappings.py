"""Tests for integration_mappings CRUD operations (CJI-02)."""
import uuid

import pytest

from src.models.integration import IntegrationMapping
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store():
    s = SQLiteStateStore(db_path=f"/tmp/test_cji_mappings_{uuid.uuid4().hex}.db")
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_upsert_and_get(store):
    m = IntegrationMapping(
        mapping_id=f"map-{uuid.uuid4().hex[:8]}",
        project_id="proj-test",
        entity_type="prd",
        entity_id="art-abc123",
        integration="confluence",
        external_ref="12345678",
        external_url="https://example.atlassian.net/wiki/spaces/TEST/pages/12345678",
        sync_status="ok",
    )
    await store.upsert_integration_mapping(m)
    got = await store.get_integration_mapping("proj-test", "prd", "art-abc123", "confluence")
    assert got is not None
    assert got.external_ref == "12345678"


@pytest.mark.asyncio
async def test_upsert_replaces(store):
    """Re-upsert with same keys updates the row (INSERT OR REPLACE)."""
    m1 = IntegrationMapping(
        mapping_id="map-001", project_id="proj-test",
        entity_type="epic", entity_id="epic-001", integration="jira",
        external_ref="PROJ-1", external_url="https://jira/PROJ-1",
    )
    await store.upsert_integration_mapping(m1)
    m2 = IntegrationMapping(
        mapping_id="map-002", project_id="proj-test",
        entity_type="epic", entity_id="epic-001", integration="jira",
        external_ref="PROJ-2", external_url="https://jira/PROJ-2",
        sync_status="error", sync_error="test",
    )
    await store.upsert_integration_mapping(m2)
    got = await store.get_integration_mapping("proj-test", "epic", "epic-001", "jira")
    assert got.external_ref == "PROJ-2"
    assert got.sync_status == "error"


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(store):
    got = await store.get_integration_mapping("proj-X", "task", "task-999", "jira")
    assert got is None


@pytest.mark.asyncio
async def test_delete(store):
    m = IntegrationMapping(
        mapping_id="map-del", project_id="proj-test",
        entity_type="feature", entity_id="feat-001", integration="jira",
        external_ref="PROJ-10", external_url="https://jira/PROJ-10",
    )
    await store.upsert_integration_mapping(m)
    deleted = await store.delete_integration_mapping("map-del")
    assert deleted is True
    got = await store.get_integration_mapping("proj-test", "feature", "feat-001", "jira")
    assert got is None
