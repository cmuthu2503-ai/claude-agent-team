"""Tests for JiraCloudClient (unit tests with mocked atlassian.Jira).

The Jira class is lazy-loaded inside _get_client(). These tests set
_client directly to avoid patching the import at module level."""
import pytest
from unittest.mock import MagicMock

from src.integrations.jira import JiraCloudClient


@pytest.mark.asyncio
async def test_upsert_epic_creates_new():
    mock_jira = MagicMock()
    mock_jira.create_issue.return_value = {"key": "PROJ-42"}

    client = JiraCloudClient(
        url="https://test.atlassian.net",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_jira
    result = await client.upsert_epic("PROJ", "My Epic", "Description")
    assert result.ok is True
    assert result.issue_key == "PROJ-42"
    assert result.action == "created"


@pytest.mark.asyncio
async def test_upsert_epic_updates_existing():
    mock_jira = MagicMock()

    client = JiraCloudClient(
        url="https://test.atlassian.net",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_jira
    result = await client.upsert_epic("PROJ", "Updated Epic", "New Desc", existing_key="PROJ-42")
    assert result.ok is True
    assert result.action == "updated"


@pytest.mark.asyncio
async def test_upsert_story_creates_new():
    mock_jira = MagicMock()
    mock_jira.create_issue.return_value = {"key": "PROJ-43"}
    mock_jira.get_all_fields.return_value = [{"id": "customfield_10014", "name": "Epic Link"}]

    client = JiraCloudClient(
        url="https://test.atlassian.net",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_jira
    result = await client.upsert_story("PROJ", "PROJ-42", "My Story", "Story desc")
    assert result.ok is True
    assert result.issue_key == "PROJ-43"
    assert result.action == "created"


@pytest.mark.asyncio
async def test_transition_issue():
    mock_jira = MagicMock()
    mock_jira.get_issue_transitions.return_value = [
        {"id": "21", "name": "In Progress"},
        {"id": "31", "name": "Done"},
    ]

    client = JiraCloudClient(
        url="https://test.atlassian.net",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_jira
    result = await client.transition_issue("PROJ-42", "In Progress")
    assert result.ok is True
    assert result.action == "transitioned"


@pytest.mark.asyncio
async def test_transition_issue_no_matching_transition():
    mock_jira = MagicMock()
    mock_jira.get_issue_transitions.return_value = [
        {"id": "31", "name": "Done"},
    ]

    client = JiraCloudClient(
        url="https://test.atlassian.net",
        email="test@test.com", api_token="tok",
    )
    client._client = mock_jira
    result = await client.transition_issue("PROJ-42", "In Progress")
    assert result.ok is False
