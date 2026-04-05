"""BaseAgent — abstract class implementing the iterative tool-use loop."""

from abc import ABC, abstractmethod
from typing import Any

import structlog

logger = structlog.get_logger()


class BaseAgent(ABC):
    """Abstract base class for all agents in the system.

    Implements the core loop: receive task → call LLM → handle tool calls → return result.
    Concrete agents configure their system prompt, model, and available tools via YAML config.
    """

    def __init__(
        self,
        agent_id: str,
        display_name: str,
        role: str,
        team: str,
        model: str,
        system_prompt: str,
        tools: list[str],
        delegation_targets: list[str],
        max_concurrent_tasks: int = 3,
    ) -> None:
        self.agent_id = agent_id
        self.display_name = display_name
        self.role = role
        self.team = team
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools
        self.delegation_targets = delegation_targets
        self.max_concurrent_tasks = max_concurrent_tasks
        self._llm_client: Any = None
        self._tool_registry: Any = None

    def set_llm_client(self, client: Any) -> None:
        """Inject the Anthropic client."""
        self._llm_client = client

    def set_tool_registry(self, registry: Any) -> None:
        """Inject the tool registry for tool execution."""
        self._tool_registry = registry

    async def process_task(
        self, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Main entry point: process a task and return results.

        Implements the iterative tool-use loop:
        1. Build messages from inputs
        2. Call LLM with system prompt + tools
        3. If LLM returns tool_use, execute tools and loop
        4. If LLM returns text, extract result and return
        """
        logger.info(
            "agent_processing_task",
            agent=self.agent_id, request_id=request_id,
        )

        messages = self._build_messages(inputs)
        tool_schemas = self._get_tool_schemas()
        total_input_tokens = 0
        total_output_tokens = 0
        llm_calls = 0
        tool_call_count = 0
        max_iterations = 20

        for _ in range(max_iterations):
            if not self._llm_client:
                # No LLM client — return mock result for testing
                return self._mock_result(inputs)

            response = await self._call_llm(messages, tool_schemas)
            llm_calls += 1
            total_input_tokens += response.get("input_tokens", 0)
            total_output_tokens += response.get("output_tokens", 0)

            # Check if response contains tool calls
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                tool_results = []
                for tool_call in tool_calls:
                    tool_call_count += 1
                    result = await self._execute_tool(
                        tool_call["name"], tool_call["input"]
                    )
                    tool_results.append({
                        "tool_use_id": tool_call["id"],
                        "content": result,
                    })
                # Add assistant message and tool results to conversation
                messages.append({"role": "assistant", "content": response["content"]})
                messages.append({"role": "user", "content": tool_results})
                continue

            # No tool calls — extract final text result
            text_output = response.get("text", "")
            return {
                "status": "completed",
                "outputs": self._parse_output(text_output),
                "artifacts": self._extract_artifacts(text_output),
                "llm_calls": llm_calls,
                "tool_calls": tool_call_count,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            }

        logger.warning("agent_max_iterations_reached", agent=self.agent_id)
        return {
            "status": "completed",
            "outputs": {},
            "artifacts": [],
            "llm_calls": llm_calls,
            "tool_calls": tool_call_count,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }

    def _build_messages(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        """Build the initial message list from task inputs."""
        content_parts = []
        for key, value in inputs.items():
            if isinstance(value, str):
                content_parts.append(f"## {key}\n{value}")
            else:
                content_parts.append(f"## {key}\n{value!r}")
        user_message = "\n\n".join(content_parts) if content_parts else "Process this task."
        return [{"role": "user", "content": user_message}]

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        """Get tool schemas for the LLM from the tool registry."""
        if not self._tool_registry:
            return []
        return self._tool_registry.get_schemas_for_agent(self.agent_id)

    async def _call_llm(
        self, messages: list[dict], tool_schemas: list[dict]
    ) -> dict[str, Any]:
        """Call the Anthropic API. Override for custom behavior."""
        response = await self._llm_client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=self.system_prompt,
            messages=messages,
            tools=tool_schemas if tool_schemas else None,
        )

        # Parse response into normalized format
        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "content": response.content,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool via the tool registry."""
        if not self._tool_registry:
            return f"Tool '{tool_name}' not available (no registry)"
        return await self._tool_registry.execute(
            tool_name=tool_name,
            agent_id=self.agent_id,
            params=tool_input,
        )

    def _mock_result(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Return a mock result when no LLM client is configured (for testing)."""
        return {
            "status": "completed",
            "outputs": {f"{self.agent_id}_output": f"Mock output from {self.display_name}"},
            "artifacts": [],
            "llm_calls": 0,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    @abstractmethod
    def _parse_output(self, text: str) -> dict[str, Any]:
        """Parse LLM text output into structured data. Agent-specific."""
        ...

    @abstractmethod
    def _extract_artifacts(self, text: str) -> list[str]:
        """Extract artifact file paths from LLM output. Agent-specific."""
        ...

    def can_delegate_to(self, target_agent_id: str) -> bool:
        """Check if this agent can delegate to the target."""
        return target_agent_id in self.delegation_targets

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.agent_id} model={self.model}>"
