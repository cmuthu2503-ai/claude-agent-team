"""Dispatcher — routes tasks to agents based on delegation rules and team hierarchy."""

from typing import Any

import structlog

from src.config.loader import ConfigLoader

logger = structlog.get_logger()


class DispatchError(Exception):
    """Raised when delegation is invalid."""


class Dispatcher:
    """Validates and routes task delegations based on config rules."""

    def __init__(self, config: ConfigLoader) -> None:
        self.config = config
        self._delegation_map: dict[str, list[str]] = {}
        self._team_domains: dict[str, list[str]] = {}
        self._build_maps()

    def _build_maps(self) -> None:
        for agent_id, agent_config in self.config.agents.items():
            delegation = agent_config.get("delegation", {})
            self._delegation_map[agent_id] = delegation.get("can_delegate_to", [])

        for team_id, team_config in self.config.teams.get("teams", {}).items():
            self._team_domains[team_id] = team_config.get("domain", [])

    def validate_delegation(self, from_agent: str, to_agent: str) -> bool:
        allowed = self._delegation_map.get(from_agent, [])
        return to_agent in allowed

    def dispatch(self, from_agent: str, to_agent: str, task: dict[str, Any]) -> dict[str, Any]:
        if not self.validate_delegation(from_agent, to_agent):
            raise DispatchError(
                f"Agent '{from_agent}' cannot delegate to '{to_agent}'. "
                f"Allowed targets: {self._delegation_map.get(from_agent, [])}"
            )
        logger.info("task_dispatched", from_agent=from_agent, to_agent=to_agent)
        return {"agent_id": to_agent, "task": task, "dispatched": True}

    def route_by_domain(self, domain_keywords: list[str]) -> str | None:
        for team_id, domains in self._team_domains.items():
            if "all" in domains:
                continue
            for keyword in domain_keywords:
                if keyword.lower() in [d.lower() for d in domains]:
                    team_config = self.config.teams.get("teams", {}).get(team_id, {})
                    return team_config.get("lead")
        return "engineering_lead"

    def get_delegation_targets(self, agent_id: str) -> list[str]:
        return self._delegation_map.get(agent_id, [])
