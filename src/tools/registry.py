"""Tool registry — loads tools, validates permissions, provides schemas for LLM."""

from typing import Any

import structlog

from src.config.loader import ConfigLoader

logger = structlog.get_logger()


class ToolPermissionError(Exception):
    """Agent does not have permission to use this tool."""


class ToolRegistry:
    """Central registry for all tools. Validates permissions and provides LLM schemas."""

    def __init__(self, config: ConfigLoader) -> None:
        self.config = config
        self._tools: dict[str, dict[str, Any]] = {}
        self._implementations: dict[str, Any] = {}
        self._load_tools()

    def _load_tools(self) -> None:
        raw_tools = self.config.tools.get("tools", {})
        for tool_id, tool_config in raw_tools.items():
            self._tools[tool_id] = {
                "tool_id": tool_id,
                "description": tool_config.get("description", ""),
                "category": tool_config.get("category", ""),
                "available_to": tool_config.get("available_to", []),
            }

    def register_implementation(self, tool_id: str, implementation: Any) -> None:
        """Register a concrete tool implementation."""
        self._implementations[tool_id] = implementation

    def is_permitted(self, tool_id: str, agent_id: str) -> bool:
        """Check if an agent has permission to use a tool."""
        tool = self._tools.get(tool_id)
        if not tool:
            return False
        available_to = tool["available_to"]
        return "all" in available_to or agent_id in available_to

    def get_schemas_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """Get tool schemas for an agent (only tools they're permitted to use)."""
        # Get the agent's configured tools from the agent config
        agent_config = self.config.agents.get(agent_id, {})
        agent_tools = agent_config.get("tools", [])

        schemas = []
        for tool_id in agent_tools:
            if not self.is_permitted(tool_id, agent_id):
                continue
            impl = self._implementations.get(tool_id)
            if impl and hasattr(impl, "schema"):
                schemas.append(impl.schema())
            else:
                # Generate a basic schema from config
                tool = self._tools.get(tool_id, {})
                schemas.append({
                    "name": tool_id,
                    "description": tool.get("description", ""),
                    "input_schema": {"type": "object", "properties": {}},
                })
        return schemas

    async def execute(self, tool_name: str, agent_id: str, params: dict) -> str:
        """Execute a tool, checking permissions first."""
        if not self.is_permitted(tool_name, agent_id):
            raise ToolPermissionError(
                f"Agent '{agent_id}' does not have permission to use tool '{tool_name}'"
            )
        impl = self._implementations.get(tool_name)
        if not impl:
            return f"Tool '{tool_name}' has no implementation registered"
        return await impl.execute(params)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def list_tools_for_agent(self, agent_id: str) -> list[str]:
        agent_config = self.config.agents.get(agent_id, {})
        return [t for t in agent_config.get("tools", []) if self.is_permitted(t, agent_id)]
