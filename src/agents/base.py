"""BaseAgent — abstract class implementing the iterative tool-use loop.

Uses the AnthropicAWS client (Claude Platform on AWS). The Messages API surface
is identical to the first-party Anthropic API, so this code is the standard
`messages.create(...)` loop plus a per-request `inference_geo` parameter for
US-pinned vs global data routing.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# Substrings that identify a transient network failure in the LLM call's
# exception message. When any of these appear in the stringified error,
# the retry loop treats the failure as retryable (with backoff) instead
# of bubbling it to the workflow as a permanent agent failure.
#
# Curated from REQ-E3A10E (T-acb5ab46) on 2026-05-22 — a ~1-2 min network
# blip on the host produced all three patterns in sequence:
#   - Anthropic streaming gave httpx-equivalent "peer closed connection
#     without sending complete message body (incomplete chunked read)"
#   - subsequent calls raised "Connection error." (anthropic.APIConnectionError)
#   - GitHub publish raised "[Errno -2] Name or service not known" (DNS)
#
# Keep this list lowercase; matching is case-insensitive. Add new
# patterns ONLY when you've seen them produce a permanent agent failure
# for what was actually a transient blip.
_TRANSIENT_NETWORK_ERROR_FRAGMENTS: tuple[str, ...] = (
    "peer closed connection",
    "incomplete chunked read",
    "incomplete read",
    "connection error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "remote disconnected",
    "name or service not known",   # DNS
    "temporary failure in name resolution",
    "timed out",                   # generic socket timeout (separate from asyncio.TimeoutError)
    "broken pipe",
    "ssl: unexpected_eof",
    "read timeout",
    "remoteprotocolerror",
)


def _is_transient_network_error(error_str: str) -> bool:
    """True if the error string looks like a transient network blip
    (DNS, connection reset, mid-stream disconnect, etc.) — the kind
    that retries with backoff can outwait."""
    if not error_str:
        return False
    s = error_str.lower()
    return any(frag in s for frag in _TRANSIENT_NETWORK_ERROR_FRAGMENTS)


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
        max_iterations: int = 15,
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
        # Per-agent tool-loop iteration budget. Was hardcoded at 5 — too low for
        # agents that need to file_read several files before writing code (see
        # REQ-7F2E07 / REQ-5858F5 backend_specialist running out of turns mid-
        # exploration). Default bumped to 15; can be overridden in agent YAML
        # via `max_iterations: N` for code-heavy agents that need more, or
        # lowered for simple agents like prd_specialist.
        self.max_iterations = max_iterations
        self._llm_client: Any = None
        self._tool_registry: Any = None
        self._inference_geo: str | None = None

    def set_llm_client(self, client: Any) -> None:
        self._llm_client = client

    def set_tool_registry(self, registry: Any) -> None:
        self._tool_registry = registry

    def set_inference_geo(self, geo: str | None) -> None:
        """Pin inference to a geography per Claude Platform on AWS docs.

        Accepted values: "us" (1.1x pricing, US data centers) or None (global,
        standard pricing). Set on every messages.create() call.
        """
        self._inference_geo = geo

    async def process_task(
        self, request_id: str, inputs: dict[str, Any],
        *, project_root: "Path | None" = None,
    ) -> dict[str, Any]:
        """Iterative tool-use loop: messages → LLM → tools → repeat until text.

        ``project_root`` (when set by the executor for a per-project
        Request) is stashed on ``self._current_project_root`` and
        forwarded to every tool invocation. Filesystem tools then
        resolve paths against this root instead of the platform default
        — without this, an agent task on CrewAI could (and did) scribble
        into the platform's ``frontend/src/App.tsx``. See file_tools.py
        for the resolution logic.
        """
        # Stashed so _execute_tool can forward without an explicit pass.
        self._current_project_root: "Path | None" = project_root
        logger.info(
            "agent_processing_task",
            agent=self.agent_id, request_id=request_id,
            project_root=str(project_root) if project_root else "platform",
        )

        if not self._llm_client:
            return self._mock_result(inputs)

        messages = self._build_messages(inputs)
        tool_schemas = self._get_tool_schemas()
        total_input_tokens = 0
        total_output_tokens = 0
        llm_calls = 0
        tool_call_count = 0
        max_iterations = self.max_iterations
        all_text_outputs: list[str] = []

        for _iteration in range(max_iterations):
            # Pass the per-agent max_tokens default. Code-writing agents
            # get 32K (engages streaming) so multi-file emissions don't
            # truncate; other agents stay at the 8K default to keep the
            # non-streaming path fast.
            response = await self._call_anthropic(
                messages, tool_schemas,
                max_tokens=self._default_max_tokens(),
            )
            llm_calls += 1
            total_input_tokens += response.get("input_tokens", 0)
            total_output_tokens += response.get("output_tokens", 0)
            # Surface truncation explicitly. When the model hits the
            # max_tokens cap the response is incomplete — log it so
            # the agent's failure is debuggable from the trace.
            stop_reason = response.get("stop_reason")
            if stop_reason == "max_tokens":
                logger.warning(
                    "agent_response_truncated_at_max_tokens",
                    agent=self.agent_id, request_id=request_id,
                    iteration=_iteration,
                    output_tokens=response.get("output_tokens", 0),
                    max_tokens=self._default_max_tokens(),
                    hint=(
                        "The response was cut off at the token cap. The "
                        "next rework cycle will see the truncated emission. "
                        "If this is a code agent, the file likely ends "
                        "mid-statement and ruff will flag invalid syntax."
                    ),
                )

            text = response.get("text", "")
            if text.strip():
                all_text_outputs.append(text)

            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                tool_exec_results: list[tuple[str, str]] = []
                for tool_call in tool_calls:
                    tool_call_count += 1
                    result = await self._execute_tool(tool_call["name"], tool_call["input"])
                    tool_exec_results.append((tool_call["id"], str(result)))

                messages.append({"role": "assistant", "content": response["content"]})
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tc_id, "content": tc_result}
                        for tc_id, tc_result in tool_exec_results
                    ],
                })
                continue

            final_text = "\n\n".join(all_text_outputs) if all_text_outputs else text
            return self._build_result(
                final_text, llm_calls, tool_call_count,
                total_input_tokens, total_output_tokens,
            )

        logger.warning(
            "agent_max_iterations_reached",
            agent=self.agent_id, iterations=max_iterations,
        )
        final_text = (
            "\n\n".join(all_text_outputs) if all_text_outputs
            else "(Agent reached max tool iterations)"
        )
        return self._build_result(
            final_text, llm_calls, tool_call_count,
            total_input_tokens, total_output_tokens,
        )

    async def single_call(
        self,
        prompt: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One-shot LLM call: no tool-use loop, no `inputs` formatting.

        Used by Project-driven Build (PDB-05) for generating per-project
        artifacts like a PRD or tasks list. The agent's `system_prompt` is
        applied; `prompt` becomes the single user message; the response is
        returned as `{text, input_tokens, output_tokens, model}` so the
        caller can persist content + cost.

        ``max_tokens`` overrides the default 8192-token cap. Long-form
        generators (PRD ≥ 60 KB, API spec ≥ 30 KB) need 32 000+ here so
        the output doesn't get truncated mid-document.

        Returns a stub result in mock mode (no LLM client configured) so
        dev environments still exercise the wire-up.
        """
        if not self._llm_client:
            return {
                "text": f"(mock {self.agent_id} output for prompt: {prompt[:80]}...)",
                "input_tokens": 0,
                "output_tokens": 0,
                "model": self.model,
            }
        response = await self._call_anthropic(
            messages=[{"role": "user", "content": prompt}],
            tool_schemas=[],
            max_tokens=max_tokens,
        )
        return {
            "text": response.get("text", ""),
            "input_tokens": response.get("input_tokens", 0),
            "output_tokens": response.get("output_tokens", 0),
            "model": self.model,
        }

    def _build_result(
        self, text: str, llm_calls: int, tool_calls: int,
        input_tokens: int, output_tokens: int
    ) -> dict[str, Any]:
        return {
            "status": "completed",
            "text": text,
            "outputs": self._parse_output(text),
            "artifacts": self._extract_artifacts(text),
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": self.model,
        }

    def _build_messages(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
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
        """Inject the current date AND the cross-agent lessons learned
        so the model doesn't fall back to its training-era clock or
        repeat known failure patterns.

        The lessons doc lives in the repo at ``docs/agent-lessons-learned.md``
        — it's a versioned, runtime-loaded record of every production
        failure pattern we've observed and the fix for it. Loaded fresh
        on EVERY system-prompt build so a freshly-added lesson is
        picked up by the next agent invocation without a code change
        or container restart. Only code-writing agents see it; PRD /
        story / research / content agents don't (their lessons live
        in their own YAML).
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
        lessons_block = self._load_cross_agent_lessons()
        return date_header + lessons_block + self.system_prompt

    # Agents that consume cross-agent lessons. Other agents (PRD,
    # user_story, research, content) don't write code so the lessons
    # would be noise in their context budget — keep their prompt lean.
    _LESSONS_CONSUMER_AGENTS: frozenset[str] = frozenset({
        "backend_specialist",
        "frontend_specialist",
        "code_reviewer",
        "tester_specialist",
        "devops_specialist",
    })

    # Default max_tokens budget for the tool-use loop's LLM calls.
    # Code-writing agents emit multi-file ``### File:`` blocks in a
    # single response — at 8192 tokens (~800-1000 LOC) the response
    # gets truncated mid-emission for any non-trivial task. The
    # T-6144cc94 RCA showed this killed 4 dispatches in a row:
    # mid-string-literal cuts at line 306, missing closing braces,
    # and "no code files produced" outcomes. 32K is Opus 4.7's
    # actual max output; it engages the streaming path (above the
    # 16K _STREAMING_MAX_TOKENS_THRESHOLD) which the Anthropic SDK
    # requires for long generations.
    _CODE_AGENT_MAX_TOKENS = 32_000
    _DEFAULT_MAX_TOKENS = 8192

    def _default_max_tokens(self) -> int:
        """Per-agent default max_tokens for the tool-use loop.

        Code-writing agents (the same set that consumes the lessons
        doc) emit large multi-file blocks per response. They need
        room — 32K engages streaming and gives ~3-5K LOC of output
        per response, enough for the realistic feature-size tasks
        the orchestrator dispatches.

        Other agents (PRD, user stories, etc.) stay at 8K — their
        outputs are short structured documents and the lower cap
        keeps non-streaming latency low for the orchestrator loop.
        """
        if self.agent_id in self._LESSONS_CONSUMER_AGENTS:
            return self._CODE_AGENT_MAX_TOKENS
        return self._DEFAULT_MAX_TOKENS

    def _load_cross_agent_lessons(self) -> str:
        """Load docs/agent-lessons-learned.md and wrap it for inclusion
        in the system prompt. Soft-fails (returns "") if the file is
        missing — preserves agent operation even if the doc gets
        renamed or deleted.

        File path is resolved RELATIVE TO THIS MODULE so the loader
        works whether the backend runs from /app/ in Docker or from
        the repo root in unit tests."""
        if self.agent_id not in self._LESSONS_CONSUMER_AGENTS:
            return ""
        try:
            from pathlib import Path as _Path
            # src/agents/base.py → repo root is parents[2]
            repo_root = _Path(__file__).resolve().parents[2]
            lessons_path = repo_root / "docs" / "agent-lessons-learned.md"
            if not lessons_path.is_file():
                return ""
            text = lessons_path.read_text(encoding="utf-8")
            # Hard cap so a runaway doc edit doesn't blow the prompt
            # budget. 30KB ≈ 8K tokens, plenty for the global lessons.
            if len(text) > 30_000:
                text = text[:30_000] + "\n\n[... truncated; trim docs/agent-lessons-learned.md]"
            return (
                "=== CROSS-AGENT LESSONS LEARNED (read this BEFORE acting on the task) ===\n"
                "This is the canonical record of failure patterns we've observed in\n"
                "production and how to avoid them. New lessons are appended over time;\n"
                "they apply to EVERY code-writing task you handle.\n\n"
                f"{text}\n\n"
                "=== END LESSONS ===\n\n"
            )
        except Exception as e:  # noqa: BLE001 — never block agent on doc-load error
            logger.warning(
                "agent_lessons_doc_load_failed",
                agent_id=self.agent_id, err=str(e),
            )
            return ""

    # Hard wall-clock timeout for a single LLM call. The Anthropic SDK does not
    # impose its own end-to-end timeout — long-tail requests can hang indefinitely
    # (REQ-5858F5's backend_specialist sat blocked >15 minutes on a single call
    # with no log activity). 180s is generous enough for normal Opus 4.7 latency
    # (typical 10-40s) while bounding pathological hangs. After timeout we treat
    # it as a retryable failure: bubble out of the retry loop, the runner sees a
    # failed subtask, request is marked failed cleanly.
    _LLM_CALL_TIMEOUT_SECONDS = 180

    # Longer timeout for streaming calls (long-form generators). The
    # Anthropic SDK refuses non-streaming requests with max_tokens >
    # ~21 000 because "operations that may take longer than 10 minutes
    # require streaming." Above _STREAMING_MAX_TOKENS_THRESHOLD we
    # switch to messages.stream() and grant a 15-minute wall-clock
    # ceiling — enough headroom for 32K-token outputs.
    _LLM_STREAMING_TIMEOUT_SECONDS = 900
    _STREAMING_MAX_TOKENS_THRESHOLD = 16_000

    async def _call_anthropic(
        self,
        messages: list[dict],
        tool_schemas: list[dict],
        max_tokens: int | None = None,
        disable_parallel_tool_use: bool = False,
    ) -> dict[str, Any]:
        """Call the Messages API on Claude Platform on AWS with retry
        on rate limits.

        ``max_tokens`` defaults to 8192 (the safe value for tool-use
        loops where each turn is bounded). Long-form one-shot generators
        (PRD, API spec, tasks) pass a higher value here — Claude Opus
        4.7 supports up to 32K output tokens.

        Above ``_STREAMING_MAX_TOKENS_THRESHOLD`` the call uses
        ``messages.stream()`` (the SDK refuses non-streaming requests
        with very high max_tokens because they may exceed the 10-minute
        cap). The text + tool_calls are collected from the streamed
        events; the final Message object provides the usage counts.

        ``disable_parallel_tool_use`` forces the model to call tools
        one-at-a-time within a turn. Default off — most tool-use loops
        benefit from parallel calls. Set True for the project
        orchestrator, where the model needs to read list_tasks output
        BEFORE deciding whether to dispatch (otherwise it fires
        list_tasks + dispatch_task × N in parallel and the dispatches
        fail because the list status hasn't been observed yet).
        """
        import asyncio as _asyncio

        effective_max_tokens = max_tokens if max_tokens is not None else 8192
        use_streaming = effective_max_tokens > self._STREAMING_MAX_TOKENS_THRESHOLD
        timeout_seconds = (
            self._LLM_STREAMING_TIMEOUT_SECONDS
            if use_streaming else self._LLM_CALL_TIMEOUT_SECONDS
        )

        max_retries = 5
        for attempt in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": effective_max_tokens,
                    "system": self._build_system_prompt(),
                    "messages": messages,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                    if disable_parallel_tool_use:
                        # tool_choice.auto + disable_parallel_tool_use:
                        # model picks WHETHER to call a tool, but if
                        # it does, only one at a time. See Anthropic
                        # docs for the exact shape.
                        kwargs["tool_choice"] = {
                            "type": "auto",
                            "disable_parallel_tool_use": True,
                        }
                if self._inference_geo:
                    # Per docs: inference_geo is rejected on models older than 4.6.
                    # claude-opus-4-7 supports it; legacy YAML overrides do not.
                    kwargs["inference_geo"] = self._inference_geo

                if use_streaming:
                    response = await _asyncio.wait_for(
                        self._stream_messages(kwargs),
                        timeout=timeout_seconds,
                    )
                else:
                    # Wrap the LLM call in asyncio.wait_for so a hung connection
                    # raises TimeoutError after _LLM_CALL_TIMEOUT_SECONDS instead of
                    # blocking the workflow indefinitely.
                    response = await _asyncio.wait_for(
                        self._llm_client.messages.create(**kwargs),
                        timeout=timeout_seconds,
                    )

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
                    # Surfaced so the tool-use loop can detect cap-hit
                    # truncation. "max_tokens" means the response was
                    # incomplete; "end_turn" / "tool_use" / "stop_sequence"
                    # mean the model finished cleanly.
                    "stop_reason": getattr(response, "stop_reason", None),
                }

            except _asyncio.TimeoutError:
                # The LLM call hung past _LLM_CALL_TIMEOUT_SECONDS. Treat as
                # retryable (like a rate limit) — the network/Anthropic side
                # may recover, but if it doesn't, we want to fail cleanly after
                # the retry budget instead of blocking forever.
                logger.warning(
                    "llm_call_timeout_retrying",
                    agent=self.agent_id, attempt=attempt + 1,
                    timeout_seconds=self._LLM_CALL_TIMEOUT_SECONDS,
                )
                if attempt < max_retries - 1:
                    await _asyncio.sleep(5)
                    continue
                # Out of retries — propagate so the workflow can mark the
                # subtask failed rather than waiting indefinitely.
                raise RuntimeError(
                    f"LLM call timed out after {max_retries} retries "
                    f"(each capped at {self._LLM_CALL_TIMEOUT_SECONDS}s)",
                )
            except Exception as e:
                error_str = str(e)
                # ── Rate limit (429) — existing behaviour, backoff 30/60/90/120s ──
                if "429" in error_str or "rate_limit" in error_str:
                    wait = min(30 * (attempt + 1), 120)
                    logger.warning(
                        "rate_limited_retrying",
                        agent=self.agent_id, attempt=attempt + 1,
                        wait=wait, error=error_str[:100],
                    )
                    await _asyncio.sleep(wait)
                    continue

                # ── Transient network errors — added 2026-05-22 after REQ-E3A10E.
                # A ~1-2 min network blip cascaded through review + test + commit
                # for T-acb5ab46:
                #   - "peer closed connection without sending complete message
                #     body (incomplete chunked read)" — Anthropic stream died
                #     mid-response (httpx.RemoteProtocolError equivalent)
                #   - "Connection error." — anthropic.APIConnectionError
                #   - "Name or service not known" — DNS failure
                # All three are transient. The previous code re-raised them and
                # the workflow marked the subtask failed permanently, which
                # consumed a rework cycle each. Treat them as retryable with
                # backoff that lasts long enough to outwait realistic blips
                # (cumulative ~4 min over 5 attempts).
                if _is_transient_network_error(error_str):
                    # Exponential-ish: 5, 15, 30, 60, 120 — total ~230s budget
                    wait = [5, 15, 30, 60, 120][min(attempt, 4)]
                    logger.warning(
                        "network_error_retrying",
                        agent=self.agent_id, attempt=attempt + 1,
                        wait=wait, error=error_str[:200],
                    )
                    if attempt < max_retries - 1:
                        await _asyncio.sleep(wait)
                        continue
                    # Out of retries — surface a clear error so the rework
                    # loop's cycle counter doesn't get poisoned by a
                    # transient network issue (the agent had no chance to
                    # produce output, so reworking is pointless).
                    raise RuntimeError(
                        f"LLM call failed after {max_retries} retries due to "
                        f"transient network error: {error_str[:200]}",
                    )
                raise

        raise RuntimeError(f"Rate limit exceeded after {max_retries} retries")

    async def _stream_messages(self, kwargs: dict[str, Any]) -> Any:
        """Run a streamed messages.create() call and return a Message-
        shaped object compatible with the non-streaming response.

        Why streaming: the Anthropic SDK refuses non-streaming requests
        when ``max_tokens`` is large enough that the response could
        exceed 10 minutes ("Streaming is required for operations that
        may take longer than 10 minutes"). The long-form generators
        (PRD, API spec, tasks) need 32K-token outputs and trip this
        check; streaming is the supported escape hatch.

        We collect chunks and let the SDK accumulate them; ``await
        stream.get_final_message()`` returns the same Message shape as
        ``messages.create()`` (with ``.content``, ``.usage``, etc.) so
        the caller doesn't need a separate code path."""
        async with self._llm_client.messages.stream(**kwargs) as stream:
            # Iterate the stream so the SDK accumulates content blocks
            # and usage counters. We don't need per-chunk handling here
            # — the platform's downstream UX is "give me the full
            # text" not "live token stream". For a streaming UI we'd
            # forward `event` over a WebSocket inside this loop.
            async for _event in stream:
                pass
            return await stream.get_final_message()

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if not self._tool_registry:
            return f"Tool '{tool_name}' not available (no registry)"
        try:
            return await self._tool_registry.execute(
                tool_name=tool_name,
                agent_id=self.agent_id,
                params=tool_input,
                # Forward the per-Request project root (set in process_task)
                # so filesystem tools resolve under the per-project working
                # tree, not the platform's /app/ tree.
                project_root=getattr(self, "_current_project_root", None),
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
