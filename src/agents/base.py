"""BaseAgent — abstract class implementing the iterative tool-use loop.

Supports both the Anthropic SDK (AsyncAnthropic / AsyncAnthropicBedrock) and the
OpenAI SDK (AsyncOpenAI) as the LLM client. The tool-use loop uses an internal
neutral format ({id, name, input}) and forks provider-specific logic only at the
message-building and API-call boundaries.
"""

import json
from abc import ABC, abstractmethod
from typing import Any

import structlog

logger = structlog.get_logger()


def _is_openai_client(client: Any) -> bool:
    """Duck-type check for the OpenAI AsyncOpenAI client (vs Anthropic clients)."""
    if client is None:
        return False
    # Anthropic clients expose `.messages.create`; OpenAI clients expose
    # `.chat.completions.create`. Checking the attribute is more robust than
    # importing openai and doing isinstance (which breaks if openai is absent).
    return hasattr(client, "chat") and hasattr(getattr(client, "chat", None), "completions")


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

        is_openai = _is_openai_client(self._llm_client)
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
                # Execute tools (provider-neutral — result is a string)
                tool_exec_results: list[tuple[str, str]] = []  # [(id, result_text), ...]
                for tool_call in tool_calls:
                    tool_call_count += 1
                    result = await self._execute_tool(tool_call["name"], tool_call["input"])
                    tool_exec_results.append((tool_call["id"], str(result)))

                # Append assistant turn + tool results in provider-specific format
                if is_openai:
                    # OpenAI: assistant message with tool_calls array, then role=tool replies
                    openai_tool_calls = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["input"])
                                if not isinstance(tc["input"], str) else tc["input"],
                            },
                        }
                        for tc in tool_calls
                    ]
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": text if text else None,
                        "tool_calls": openai_tool_calls,
                    }
                    messages.append(assistant_msg)
                    for tc_id, tc_result in tool_exec_results:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": tc_result,
                        })
                else:
                    # Anthropic: assistant message is the raw content blocks;
                    # tool results go in a single user message as a list of tool_result blocks.
                    messages.append({"role": "assistant", "content": response["content"]})
                    messages.append({
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": tc_id, "content": tc_result}
                            for tc_id, tc_result in tool_exec_results
                        ],
                    })
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
            "model": self.model,  # actual model used for this call (reflects any per-call override)
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

    def _build_system_prompt(self) -> str:
        """Return the agent's system prompt with a date header injected at the top.

        Without this, the model falls back to its training-era internal clock when
        writing time references (e.g., "November 2024" instead of the real current
        date), which makes fresh web_search results useless for anything time-aware.
        """
        from datetime import datetime
        today = datetime.utcnow()
        date_header = (
            f"CURRENT DATE: {today.strftime('%Y-%m-%d')} "
            f"(year: {today.year}, month: {today.strftime('%B %Y')}).\n"
            f"When using web_search or writing ANY time reference in your output "
            f"(report titles, 'as of' statements, search queries), use THIS date as "
            f'"today" and THIS year as "current". Never default to an earlier year '
            f"from your training data.\n\n"
        )
        return date_header + self.system_prompt

    async def _call_llm(
        self, messages: list[dict], tool_schemas: list[dict]
    ) -> dict[str, Any]:
        """Dispatch to the right provider based on the injected client type."""
        if _is_openai_client(self._llm_client):
            return await self._call_openai(messages, tool_schemas)
        return await self._call_anthropic(messages, tool_schemas)

    async def _call_anthropic(
        self, messages: list[dict], tool_schemas: list[dict]
    ) -> dict[str, Any]:
        """Call the Anthropic API (direct or Bedrock) with retry on rate limits."""
        import asyncio as _asyncio

        max_retries = 5
        for attempt in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": 8192,
                    "system": self._build_system_prompt(),
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

    async def _call_openai(
        self, messages: list[dict], tool_schemas: list[dict]
    ) -> dict[str, Any]:
        """Call the OpenAI Chat Completions API with retry on rate limits.

        Converts the Anthropic-style tool schemas into OpenAI function-calling
        format on the fly, and normalizes the response back into the neutral
        shape the tool-use loop expects.
        """
        import asyncio as _asyncio

        # Inject the system prompt as the first message (OpenAI puts it in `messages`)
        oai_messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()},
            *messages,
        ]
        oai_tools = self._convert_tools_to_openai(tool_schemas) if tool_schemas else None
        # Reasoning models (o3, o4, o-series) don't accept custom temperature and use
        # max_completion_tokens instead of max_tokens — detect via model name prefix.
        is_reasoning = self.model.startswith("o") and not self.model.startswith("openai")

        max_retries = 5
        for attempt in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": oai_messages,
                }
                if is_reasoning:
                    kwargs["max_completion_tokens"] = 8192
                else:
                    kwargs["max_tokens"] = 8192
                if oai_tools:
                    kwargs["tools"] = oai_tools

                response = await self._llm_client.chat.completions.create(**kwargs)

                choice = response.choices[0]
                msg = choice.message
                text = msg.content or ""

                tool_calls: list[dict[str, Any]] = []
                for tc in (msg.tool_calls or []):
                    raw_args = tc.function.arguments or "{}"
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        parsed_args = {"_raw": raw_args}
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": parsed_args,
                    })

                usage = getattr(response, "usage", None)
                return {
                    "text": text,
                    "tool_calls": tool_calls,
                    "content": msg,  # not used by OpenAI turn-append path
                    "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                }

            except Exception as e:
                error_str = str(e)
                low = error_str.lower()
                # Permanent billing/quota errors — no amount of waiting fixes these.
                # OpenAI returns these as HTTP 429 but with code "insufficient_quota",
                # which must be distinguished from true transient rate-limit 429s.
                if (
                    "insufficient_quota" in low
                    or "exceeded your current quota" in low
                    or "billing_hard_limit_reached" in low
                ):
                    logger.error(
                        "openai_quota_exhausted",
                        agent=self.agent_id, error=error_str[:200],
                    )
                    raise RuntimeError(
                        "OpenAI API quota exhausted (insufficient_quota). "
                        "Add billing credit at https://platform.openai.com/account/billing "
                        "or re-run this request with a different provider (Sonnet / Opus / Bedrock)."
                    ) from e
                # Transient rate limit — back off and retry
                if "429" in error_str or "rate_limit" in low:
                    wait = min(30 * (attempt + 1), 120)
                    logger.warning(
                        "openai_rate_limited_retrying",
                        agent=self.agent_id, attempt=attempt + 1,
                        wait=wait, error=error_str[:100],
                    )
                    await _asyncio.sleep(wait)
                    continue
                raise

        raise RuntimeError(f"OpenAI rate limit exceeded after {max_retries} retries")

    @staticmethod
    def _convert_tools_to_openai(anthropic_schemas: list[dict]) -> list[dict]:
        """Convert Anthropic tool schemas to OpenAI function-calling schemas.

        Anthropic format: {name, description, input_schema: {type, properties, required}}
        OpenAI format:    {type: "function", function: {name, description, parameters: {...}}}
        """
        converted: list[dict[str, Any]] = []
        for t in anthropic_schemas:
            parameters = t.get("input_schema") or {"type": "object", "properties": {}}
            converted.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": parameters,
                },
            })
        return converted

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
