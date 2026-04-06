"""BaseAgent — abstract class implementing the iterative tool-use loop."""

from abc import ABC, abstractmethod
from typing import Any

import structlog

logger = structlog.get_logger()


class BaseAgent(ABC):
    """Abstract base class for all agents in the system."""

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
        self._llm_client = client

    def set_tool_registry(self, registry: Any) -> None:
        self._tool_registry = registry

    async def process_task(
        self, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Main entry point: process a task and return results.

        The iterative loop:
        1. Build messages from inputs
        2. Call LLM with system prompt + tools
        3. If LLM returns tool_use, execute tools and loop
        4. If LLM returns text, return it
        5. After max iterations, return all accumulated text
        """
        logger.info("agent_processing_task", agent=self.agent_id, request_id=request_id)

        if not self._llm_client:
            return self._mock_result(inputs)

        messages = self._build_messages(inputs)
        tool_schemas = self._get_tool_schemas()
        total_input_tokens = 0
        total_output_tokens = 0
        llm_calls = 0
        tool_call_count = 0
        max_iterations = 5
        all_text_outputs: list[str] = []  # Accumulate ALL text across iterations

        for iteration in range(max_iterations):
            response = await self._call_llm(messages, tool_schemas)
            llm_calls += 1
            total_input_tokens += response.get("input_tokens", 0)
            total_output_tokens += response.get("output_tokens", 0)

            # Capture any text from this response
            text = response.get("text", "")
            if text.strip():
                all_text_outputs.append(text)

            # Check for tool calls
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                tool_results = []
                for tool_call in tool_calls:
                    tool_call_count += 1
                    result = await self._execute_tool(tool_call["name"], tool_call["input"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": str(result),
                    })
                messages.append({"role": "assistant", "content": response["content"]})
                messages.append({"role": "user", "content": tool_results})
                continue

            # No tool calls — final text response
            final_text = "\n\n".join(all_text_outputs) if all_text_outputs else text
            return self._build_result(final_text, llm_calls, tool_call_count, total_input_tokens, total_output_tokens)

        # Max iterations reached — return all accumulated text
        logger.warning("agent_max_iterations_reached", agent=self.agent_id, iterations=max_iterations)
        final_text = "\n\n".join(all_text_outputs) if all_text_outputs else "(Agent reached max tool iterations)"
        return self._build_result(final_text, llm_calls, tool_call_count, total_input_tokens, total_output_tokens)

    def _build_result(
        self, text: str, llm_calls: int, tool_calls: int,
        input_tokens: int, output_tokens: int
    ) -> dict[str, Any]:
        """Build a standardized result dict from the agent's text output."""
        return {
            "status": "completed",
            "text": text,  # Raw text always available
            "outputs": self._parse_output(text),
            "artifacts": self._extract_artifacts(text),
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    def _build_messages(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        """Build the initial message list from task inputs."""
        content_parts = []
        for key, value in inputs.items():
            if isinstance(value, str) and len(value) > 10:
                content_parts.append(f"## {key}\n{value}")
            elif isinstance(value, dict):
                formatted = "\n".join(f"- {k}: {v}" for k, v in value.items())
                content_parts.append(f"## {key}\n{formatted}")
            elif value:
                content_parts.append(f"## {key}\n{value!r}")
        user_message = "\n\n".join(content_parts) if content_parts else "Process this task."
        return [{"role": "user", "content": user_message}]

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        if not self._tool_registry:
            return []
        return self._tool_registry.get_schemas_for_agent(self.agent_id)

    async def _call_llm(
        self, messages: list[dict], tool_schemas: list[dict]
    ) -> dict[str, Any]:
        """Call the Anthropic API with retry on rate limits."""
        import asyncio as _asyncio

        max_retries = 5
        for attempt in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": 8192,
                    "system": self.system_prompt,
                    "messages": messages,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas

                response = await self._llm_client.messages.create(**kwargs)

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

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "rate_limit" in error_str:
                    wait = min(30 * (attempt + 1), 120)
                    logger.warning(
                        "rate_limited_retrying",
                        agent=self.agent_id, attempt=attempt + 1,
                        wait=wait, error=error_str[:100],
                    )
                    await _asyncio.sleep(wait)
                    continue
                raise

        raise RuntimeError(f"Rate limit exceeded after {max_retries} retries")

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if not self._tool_registry:
            return f"Tool '{tool_name}' not available (no registry)"
        try:
            return await self._tool_registry.execute(
                tool_name=tool_name, agent_id=self.agent_id, params=tool_input,
            )
        except Exception as e:
            return f"Tool error: {e}"

    def _mock_result(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "completed",
            "text": f"Mock output from {self.display_name}",
            "outputs": {f"{self.agent_id}_output": f"Mock output from {self.display_name}"},
            "artifacts": [],
            "llm_calls": 0,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    @abstractmethod
    def _parse_output(self, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def _extract_artifacts(self, text: str) -> list[str]: ...

    def can_delegate_to(self, target_agent_id: str) -> bool:
        return target_agent_id in self.delegation_targets

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.agent_id} model={self.model}>"
