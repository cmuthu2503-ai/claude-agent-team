"""AgentFactory — creates agent instances from YAML configuration."""

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.agents.implementations import (
    BackendSpecialistAgent,
    CodeReviewerAgent,
    DevOpsSpecialistAgent,
    FrontendSpecialistAgent,
    PRDSpecialistAgent,
    TesterSpecialistAgent,
    UserStoryAuthorAgent,
)
from src.config.loader import ConfigLoader

logger = structlog.get_logger()

# Map agent_id to concrete class
AGENT_CLASS_MAP: dict[str, type[BaseAgent]] = {
    "prd_specialist": PRDSpecialistAgent,
    "user_story_author": UserStoryAuthorAgent,
    "code_reviewer": CodeReviewerAgent,
    "backend_specialist": BackendSpecialistAgent,
    "frontend_specialist": FrontendSpecialistAgent,
    "devops_specialist": DevOpsSpecialistAgent,
    "tester_specialist": TesterSpecialistAgent,
}


class AgentFactory:
    """Creates agent instances from YAML configuration files."""

    def __init__(self, config: ConfigLoader) -> None:
        self.config = config

    def create_all(self) -> dict[str, BaseAgent]:
        """Create all agents defined in config/agents/."""
        agents: dict[str, BaseAgent] = {}
        for agent_id, agent_config in self.config.agents.items():
            agent = self.create_agent(agent_id, agent_config)
            agents[agent_id] = agent
            logger.info("agent_created", agent_id=agent_id, model=agent.model)
        return agents

    def create_agent(self, agent_id: str, config: dict[str, Any]) -> BaseAgent:
        """Create a single agent from its configuration."""
        agent_class = AGENT_CLASS_MAP.get(agent_id)
        if not agent_class:
            # Default to a generic agent if no specific class exists
            logger.warning("using_generic_agent", agent_id=agent_id)
            agent_class = _GenericAgent

        delegation = config.get("delegation", {})
        return agent_class(
            agent_id=agent_id,
            display_name=config.get("display_name", agent_id),
            role=config.get("role", ""),
            team=config.get("team", ""),
            model=config.get("model", "claude-sonnet-4-6"),
            system_prompt=config.get("system_prompt", ""),
            tools=config.get("tools", []),
            delegation_targets=delegation.get("can_delegate_to", []),
            max_concurrent_tasks=delegation.get("max_concurrent_tasks", 3),
        )


class _GenericAgent(BaseAgent):
    """Fallback agent for unknown agent types."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"text": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []
