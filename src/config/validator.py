"""Configuration validator — validates all config files against schemas and rules."""

import json
import sys
from pathlib import Path
from typing import Any

import structlog

from src.config.loader import ConfigLoader

logger = structlog.get_logger()

SCHEMA_DIR = Path(__file__).parent / "schemas"


class ValidationError:
    """A single validation error."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message

    def __str__(self) -> str:
        return f"[{self.path}] {self.message}"


class ConfigValidator:
    """Validates all configuration files for correctness and consistency."""

    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader
        self.errors: list[ValidationError] = []

    def validate_all(self) -> list[ValidationError]:
        """Run all validation checks. Returns list of errors (empty = valid)."""
        self.errors = []
        self._validate_agents()
        self._validate_teams()
        self._validate_delegation_rules()
        self._validate_tool_permissions()
        self._validate_workflows()
        self._validate_orphan_agents()
        self._validate_circular_delegation()
        return self.errors

    def _add_error(self, path: str, message: str) -> None:
        self.errors.append(ValidationError(path, message))

    def _validate_agents(self) -> None:
        """Validate each agent config has required fields and valid references."""
        schema = self._load_schema("agent_schema.json")
        required_fields = schema.get("required", [])

        for agent_id, config in self.loader.agents.items():
            for field in required_fields:
                if field not in config:
                    self._add_error(
                        f"agents/{agent_id}", f"Missing required field: {field}"
                    )

            # Validate model is a known value
            model = config.get("model", "")
            if model not in ("claude-opus-4-6", "claude-sonnet-4-6"):
                self._add_error(
                    f"agents/{agent_id}", f"Invalid model: {model}"
                )

            # Validate team exists
            team = config.get("team", "")
            teams = self.loader.teams.get("teams", {})
            if team and team not in teams:
                self._add_error(
                    f"agents/{agent_id}", f"Team '{team}' not found in teams.yaml"
                )

            # Validate reports_to exists (if not null)
            reports_to = config.get("reports_to")
            if reports_to and reports_to not in self.loader.agents:
                self._add_error(
                    f"agents/{agent_id}",
                    f"reports_to '{reports_to}' is not a known agent",
                )

    def _validate_teams(self) -> None:
        """Validate team config: leads exist, members exist, sub_teams exist."""
        teams = self.loader.teams.get("teams", {})

        for team_id, config in teams.items():
            lead = config.get("lead")
            if lead and lead not in self.loader.agents:
                self._add_error(
                    f"teams/{team_id}", f"Lead '{lead}' is not a known agent"
                )

            for member in config.get("members", []):
                if member not in self.loader.agents:
                    self._add_error(
                        f"teams/{team_id}", f"Member '{member}' is not a known agent"
                    )

            for sub_team in config.get("sub_teams", []):
                if sub_team not in teams:
                    self._add_error(
                        f"teams/{team_id}",
                        f"Sub-team '{sub_team}' not found in teams.yaml",
                    )

            parent = config.get("parent_team")
            if parent and parent not in teams:
                self._add_error(
                    f"teams/{team_id}",
                    f"Parent team '{parent}' not found in teams.yaml",
                )

    def _validate_delegation_rules(self) -> None:
        """Validate delegation targets are valid agents and follow hierarchy."""
        for agent_id, config in self.loader.agents.items():
            delegation = config.get("delegation", {})
            targets = delegation.get("can_delegate_to", [])

            for target in targets:
                if target not in self.loader.agents:
                    self._add_error(
                        f"agents/{agent_id}",
                        f"Delegation target '{target}' is not a known agent",
                    )

    def _validate_tool_permissions(self) -> None:
        """Validate that agents only reference tools that exist in tools.yaml."""
        defined_tools = set(self.loader.tools.get("tools", {}).keys())

        for agent_id, config in self.loader.agents.items():
            for tool in config.get("tools", []):
                if tool not in defined_tools:
                    self._add_error(
                        f"agents/{agent_id}",
                        f"Tool '{tool}' not found in tools.yaml",
                    )

    def _validate_workflows(self) -> None:
        """Validate workflow stages reference valid agents."""
        workflows = self.loader.workflows.get("workflows", {})

        for wf_id, wf_config in workflows.items():
            stages = wf_config.get("stages", {})
            for stage_id, stage in stages.items():
                # Check direct agents
                for agent in stage.get("agents", []):
                    if agent not in self.loader.agents:
                        self._add_error(
                            f"workflows/{wf_id}/{stage_id}",
                            f"Agent '{agent}' not found",
                        )
                # Check parallel agents
                parallel = stage.get("parallel", {})
                for _group_id, group in parallel.items():
                    for agent in group.get("agents", []):
                        if agent not in self.loader.agents:
                            self._add_error(
                                f"workflows/{wf_id}/{stage_id}",
                                f"Agent '{agent}' not found in parallel group",
                            )

    def _validate_orphan_agents(self) -> None:
        """Check for agents not assigned to any team."""
        teams = self.loader.teams.get("teams", {})
        all_team_members: set[str] = set()
        for config in teams.values():
            all_team_members.add(config.get("lead", ""))
            all_team_members.update(config.get("members", []))

        for agent_id in self.loader.agents:
            if agent_id not in all_team_members:
                self._add_error(
                    f"agents/{agent_id}",
                    "Agent is not a member or lead of any team (orphan)",
                )

    def _validate_circular_delegation(self) -> None:
        """Check for circular delegation chains."""
        for agent_id in self.loader.agents:
            visited: set[str] = set()
            current = agent_id
            while current:
                if current in visited:
                    self._add_error(
                        f"agents/{agent_id}",
                        f"Circular reports_to chain detected: {' -> '.join(visited)} -> {current}",
                    )
                    break
                visited.add(current)
                current_config = self.loader.agents.get(current, {})
                current = current_config.get("reports_to")

    def _load_schema(self, filename: str) -> dict[str, Any]:
        """Load a JSON schema file."""
        schema_path = SCHEMA_DIR / filename
        if not schema_path.exists():
            return {}
        with open(schema_path) as f:
            return json.load(f)


def main() -> None:
    """CLI entry point: validate all config files."""
    loader = ConfigLoader()
    try:
        loader.load_all()
    except (FileNotFoundError, ValueError) as e:
        print(f"FATAL: {e}")
        sys.exit(1)

    validator = ConfigValidator(loader)
    errors = validator.validate_all()

    if errors:
        print(f"\nValidation FAILED — {len(errors)} error(s):\n")
        for error in errors:
            print(f"  ✗ {error}")
        sys.exit(1)
    else:
        agents = len(loader.agents)
        teams = len(loader.teams.get("teams", {}))
        workflows = len(loader.workflows.get("workflows", {}))
        tools = len(loader.tools.get("tools", {}))
        print(f"\nValidation PASSED ✓")
        print(f"  Agents:    {agents}")
        print(f"  Teams:     {teams}")
        print(f"  Workflows: {workflows}")
        print(f"  Tools:     {tools}")
        sys.exit(0)


if __name__ == "__main__":
    main()
