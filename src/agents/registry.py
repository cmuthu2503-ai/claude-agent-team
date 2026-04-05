"""AgentRegistry — indexes agents for lookup by id, role, or team."""

from src.agents.base import BaseAgent


class AgentRegistry:
    """Central registry for all agent instances. Used by dispatcher for routing."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._by_team: dict[str, list[BaseAgent]] = {}
        self._by_role: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Register an agent instance."""
        self._agents[agent.agent_id] = agent
        self._by_role[agent.role] = agent
        if agent.team not in self._by_team:
            self._by_team[agent.team] = []
        self._by_team[agent.team].append(agent)

    def register_all(self, agents: dict[str, BaseAgent]) -> None:
        """Register multiple agents at once."""
        for agent in agents.values():
            self.register(agent)

    def get(self, agent_id: str) -> BaseAgent | None:
        """Look up agent by ID."""
        return self._agents.get(agent_id)

    def get_by_role(self, role: str) -> BaseAgent | None:
        """Look up agent by role name."""
        return self._by_role.get(role)

    def get_by_team(self, team: str) -> list[BaseAgent]:
        """Get all agents in a team."""
        return self._by_team.get(team, [])

    def all_agents(self) -> list[BaseAgent]:
        """Get all registered agents."""
        return list(self._agents.values())

    def agent_ids(self) -> list[str]:
        """Get all registered agent IDs."""
        return list(self._agents.keys())

    @property
    def count(self) -> int:
        return len(self._agents)
