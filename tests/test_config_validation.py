"""P8-T01: Extended config system tests — edge cases, invalid configs."""

import pytest
import yaml
from pathlib import Path
from src.config.loader import ConfigLoader
from src.config.validator import ConfigValidator


@pytest.fixture
def config():
    loader = ConfigLoader()
    loader.load_all()
    return loader


def test_all_agents_loaded(config):
    assert len(config.agents) == 8


def test_all_teams_loaded(config):
    teams = config.teams.get("teams", {})
    assert len(teams) == 4
    assert "engineering" in teams
    assert "planning" in teams
    assert "development" in teams
    assert "delivery" in teams


def test_all_workflows_loaded(config):
    workflows = config.workflows.get("workflows", {})
    assert len(workflows) == 4


def test_all_tools_loaded(config):
    tools = config.tools.get("tools", {})
    assert len(tools) == 14


def test_validator_passes_on_valid_config(config):
    validator = ConfigValidator(config)
    errors = validator.validate_all()
    assert len(errors) == 0


def test_validator_catches_invalid_delegation(config, tmp_path):
    """Create a config with invalid delegation target and verify it's caught."""
    # Create a modified agent with bad delegation
    agents_dir = tmp_path / "config" / "agents"
    agents_dir.mkdir(parents=True)

    bad_agent = {
        "agent_id": "bad_agent",
        "display_name": "Bad Agent",
        "role": "Bad",
        "team": "development",
        "reports_to": "code_reviewer",
        "model": "claude-sonnet-4-6",
        "system_prompt": "You are bad.",
        "responsibilities": [{"id": "BAD-001", "description": "bad", "category": "development"}],
        "tools": ["file_read"],
        "outputs": [{"name": "output", "format": "code"}],
        "delegation": {"can_delegate_to": ["nonexistent_agent"], "max_concurrent_tasks": 1},
        "quality_gates": [],
        "metadata": {"created": "2026-04-05", "version": "1.0"},
    }
    (agents_dir / "bad_agent.yaml").write_text(yaml.dump(bad_agent))

    # Copy existing valid agents
    for agent_file in Path("config/agents").glob("*.yaml"):
        if not agent_file.name.startswith("_"):
            (agents_dir / agent_file.name).write_text(agent_file.read_text())

    # Copy other config files
    for f in ["teams.yaml", "workflows.yaml", "tools.yaml", "thresholds.yaml", "project.yaml"]:
        (tmp_path / "config" / f).write_text(Path(f"config/{f}").read_text())

    loader = ConfigLoader(config_dir=str(tmp_path / "config"))
    loader.load_all()
    validator = ConfigValidator(loader)
    errors = validator.validate_all()
    error_messages = [str(e) for e in errors]
    assert any("nonexistent_agent" in msg for msg in error_messages)


def test_validator_catches_orphan_agent(config, tmp_path):
    """Agent not in any team should be flagged."""
    agents_dir = tmp_path / "config" / "agents"
    agents_dir.mkdir(parents=True)

    orphan = {
        "agent_id": "orphan_agent",
        "display_name": "Orphan",
        "role": "Orphan",
        "team": "development",
        "reports_to": None,
        "model": "claude-sonnet-4-6",
        "system_prompt": "You are orphaned.",
        "responsibilities": [{"id": "O-001", "description": "orphan", "category": "development"}],
        "tools": [],
        "outputs": [{"name": "output", "format": "code"}],
        "delegation": {"can_delegate_to": [], "max_concurrent_tasks": 1},
        "quality_gates": [],
        "metadata": {"created": "2026-04-05", "version": "1.0"},
    }
    (agents_dir / "orphan_agent.yaml").write_text(yaml.dump(orphan))

    for agent_file in Path("config/agents").glob("*.yaml"):
        if not agent_file.name.startswith("_"):
            (agents_dir / agent_file.name).write_text(agent_file.read_text())

    for f in ["teams.yaml", "workflows.yaml", "tools.yaml", "thresholds.yaml", "project.yaml"]:
        (tmp_path / "config" / f).write_text(Path(f"config/{f}").read_text())

    loader = ConfigLoader(config_dir=str(tmp_path / "config"))
    loader.load_all()
    validator = ConfigValidator(loader)
    errors = validator.validate_all()
    error_messages = [str(e) for e in errors]
    assert any("orphan" in msg.lower() for msg in error_messages)


def test_thresholds_has_cost_section(config):
    cost = config.thresholds.get("cost", {})
    assert "pricing" in cost
    assert "budget" in cost
    assert cost["budget"]["daily_limit_usd"] > 0


def test_thresholds_has_testing_section(config):
    testing = config.thresholds.get("testing", {})
    assert testing["unit_coverage_min"] == 80


def test_thresholds_has_backup_section(config):
    backup = config.thresholds.get("backup", {})
    assert backup["schedule"] == "daily"
    assert backup["retention_days"] == 30


def test_project_has_auth_section(config):
    auth = config.project.get("auth", {})
    assert auth["access_token_lifetime_minutes"] == 30
    assert auth["refresh_token_lifetime_days"] == 7
    assert "roles" in auth
    assert "admin" in auth["roles"]
    assert "developer" in auth["roles"]
    assert "viewer" in auth["roles"]


def test_project_has_environments(config):
    envs = config.project.get("project", {}).get("environments", {})
    assert "local" in envs
    assert "staging" in envs
    assert "production" in envs
    assert "demo" in envs


def test_loader_raises_on_missing_file():
    loader = ConfigLoader(config_dir="nonexistent_dir")
    with pytest.raises(FileNotFoundError):
        loader.load_all()
