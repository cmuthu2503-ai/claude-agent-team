"""Configuration loader — reads all YAML config files."""

from pathlib import Path
from typing import Any

import yaml


class ConfigLoader:
    """Loads and provides access to all YAML configuration files."""

    def __init__(self, config_dir: str | Path = "config") -> None:
        self.config_dir = Path(config_dir)
        self._agents: dict[str, dict[str, Any]] = {}
        self._teams: dict[str, Any] = {}
        self._workflows: dict[str, Any] = {}
        self._tools: dict[str, Any] = {}
        self._thresholds: dict[str, Any] = {}
        self._project: dict[str, Any] = {}

    def load_all(self) -> None:
        """Load all configuration files."""
        self._agents = self._load_agents()
        self._teams = self._load_yaml("teams.yaml")
        self._workflows = self._load_yaml("workflows.yaml")
        self._tools = self._load_yaml("tools.yaml")
        self._thresholds = self._load_yaml("thresholds.yaml")
        self._project = self._load_yaml("project.yaml")

    def _load_yaml(self, filename: str) -> dict[str, Any]:
        """Load a single YAML file."""
        filepath = self.config_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Config file not found: {filepath}")
        with open(filepath) as f:
            return yaml.safe_load(f)

    def _load_agents(self) -> dict[str, dict[str, Any]]:
        """Load all agent YAML files from config/agents/."""
        agents_dir = self.config_dir / "agents"
        if not agents_dir.exists():
            raise FileNotFoundError(f"Agents directory not found: {agents_dir}")

        agents = {}
        for filepath in agents_dir.glob("*.yaml"):
            if filepath.name.startswith("_"):
                continue  # skip template
            with open(filepath) as f:
                agent_config = yaml.safe_load(f)
            agent_id = agent_config.get("agent_id")
            if not agent_id:
                raise ValueError(f"Agent config missing agent_id: {filepath}")
            agents[agent_id] = agent_config
        return agents

    @property
    def agents(self) -> dict[str, dict[str, Any]]:
        return self._agents

    @property
    def teams(self) -> dict[str, Any]:
        return self._teams

    @property
    def workflows(self) -> dict[str, Any]:
        return self._workflows

    @property
    def tools(self) -> dict[str, Any]:
        return self._tools

    @property
    def thresholds(self) -> dict[str, Any]:
        return self._thresholds

    @property
    def project(self) -> dict[str, Any]:
        return self._project
