"""Tests for ConfluenceCloudClient (unit tests with mocked atlassian.Confluence).

The Confluence class is lazy-loaded inside _get_client(). These tests set
_client directly to avoid patching the import at module level."""
import pytest
from unittest.mock import MagicMock

from src.integrations.confluence import ConfluenceCloudClient


@pytest.mark.asyncio
async def test_ensure_space_creates_when_missing():
    mock_cf = MagicMock()
    mock_cf.get_space.side_effect = Exception("not found")
    mock_cf.create_space.return_value = {"id": "space-123"}

    client = ConfluenceCloudClient(
        url="https://test.atlassian.net/wiki",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_cf
    result = await client.ensure_space("TEST", "Test Space")
    assert result["ok"] is True
    assert result["space_id"] == "space-123"
    assert result["created"] is True


@pytest.mark.asyncio
async def test_ensure_space_retrieves_existing():
    mock_cf = MagicMock()
    mock_cf.get_space.return_value = {"id": "space-456"}

    client = ConfluenceCloudClient(
        url="https://test.atlassian.net/wiki",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_cf
    result = await client.ensure_space("TEST", "Test Space")
    assert result["ok"] is True
    assert result["space_id"] == "space-456"
    assert result["created"] is False


@pytest.mark.asyncio
async def test_upsert_page_creates_new():
    mock_cf = MagicMock()
    mock_cf.create_page.return_value = {"id": "page-789"}

    client = ConfluenceCloudClient(
        url="https://test.atlassian.net/wiki",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_cf
    result = await client.upsert_page("TEST", "PRD v1", "# Hello", existing_page_id=None)
    assert result.ok is True
    assert result.page_id == "page-789"
    assert result.action == "created"


@pytest.mark.asyncio
async def test_upsert_page_updates_existing():
    mock_cf = MagicMock()

    client = ConfluenceCloudClient(
        url="https://test.atlassian.net/wiki",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_cf
    result = await client.upsert_page("TEST", "PRD v2", "# Updated", existing_page_id="page-789")
    assert result.ok is True
    assert result.action == "updated"


@pytest.mark.asyncio
async def test_upsert_page_handles_error():
    mock_cf = MagicMock()
    mock_cf.create_page.side_effect = Exception("API error")

    client = ConfluenceCloudClient(
        url="https://test.atlassian.net/wiki",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_cf
    result = await client.upsert_page("TEST", "PRD v1", "# Hello")
    assert result.ok is False
    assert "API error" in (result.error or "")
