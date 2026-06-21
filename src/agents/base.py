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


# Substrings that identify a transient failure in the LLM call's
# exception message. When any of these appear in the stringified error,
# the retry loop treats the failure as retryable (with backoff) instead
# of bubbling it to the workflow as a permanent agent failure.
#
# Curated from production incidents:
#   - REQ-E3A10E (T-acb5ab46, 2026-05-22): a ~1-2 min network blip
#     produced "peer closed connection ... (incomplete chunked read)",
#     "Connection error.", and "Name or service not known" (DNS).
#   - REQ-FC2425 (T-103e9025, 2026-05-22): Anthropic returned
#     ``{'type':'error','error':{'type':'overloaded_error','message':
#     'Overloaded'}}`` (HTTP 529). This is Anthropic's transient
#     throttle signal — by definition retryable, but the L16 retry
#     loop wasn't catching it before.
#
# Keep this list lowercase; matching is case-insensitive. Add new
# patterns ONLY when you've seen them produce a permanent agent failure
# for what was actually a transient blip.
_TRANSIENT_NETWORK_ERROR_FRAGMENTS: tuple[str, ...] = (
    # Network / TCP layer
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
    # Anthropic API-level transient signals (added after REQ-FC2425).
    # The bare ": overloaded" / "overloaded" word is sufficient — this
    # word doesn't appear in any non-transient error class we care
    # about, and the SDK surfaces it in multiple wrappings
    # (overloaded_error, "'type': 'overloaded'", "message: Overloaded",
    # plain "Overloaded").
    "overloaded",                  # HTTP 529 in any wrapping
    "service unavailable",         # generic 503
    "internal server error",       # generic 5xx (anthropic.InternalServerError)
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
        # lowered for simple agents like business_analyst.
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
        llm_client: Any = None,
        model: str | None = None,
        tool_calling_mode: str | None = None,
        kb_scope: Any = None,
        kb_retriever: Any = None,
        retrieval_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Iterative tool-use loop: messages → LLM → tools → repeat until text.

        ``project_root`` (when set by the executor for a per-project
        Request) is stashed on ``self._current_project_root`` and
        forwarded to every tool invocation. Filesystem tools then
        resolve paths against this root instead of the platform default
        — without this, an agent task on CrewAI could (and did) scribble
        into the platform's ``frontend/src/App.tsx``. See file_tools.py
        for the resolution logic.

        PAM-06: ``llm_client``, ``model``, and ``tool_calling_mode`` are
        now keyword arguments. The executor (PAM-07) resolves them
        per-invocation via ModelResolver and passes them down here.
        Each call carries its own values on the call stack rather than
        mutating ``self._llm_client`` / ``self.model`` — two concurrent
        ``process_task()`` calls on the same agent with different models
        can no longer cross-pollinate, eliminating the L24-style race
        the executor previously worked around with snapshot-and-restore.

        Defaults fall back to instance attributes so legacy callers that
        pre-set them via ``set_llm_client()`` keep working unchanged.
        """
        # Stashed so _execute_tool can forward without an explicit pass.
        self._current_project_root: "Path | None" = project_root
        # KB-09 — stash the knowledge scope so _execute_tool forwards it to
        # knowledge_search/get (the agent can't widen it). Same per-call
        # self-stash pattern as project_root above.
        self._current_kb_scope: Any = kb_scope
        # KB-09 — forced/hybrid retrieval grounding, computed once below and
        # consumed by _build_system_prompt across loop iterations.
        self._kb_grounding: str = ""
        # Per-call values resolved here as LOCALS (not self attributes) so
        # concurrent process_task() invocations on this agent each carry
        # their own values on the stack — no cross-pollination via self.
        effective_client: Any = (
            llm_client if llm_client is not None else self._llm_client
        )
        effective_model: str = model if model is not None else self.model
        effective_mode: str = (
            tool_calling_mode if tool_calling_mode is not None else "native"
        )
        logger.info(
            "agent_processing_task",
            agent=self.agent_id, request_id=request_id,
            project_root=str(project_root) if project_root else "platform",
            model=effective_model,
            tool_calling_mode=effective_mode,
        )

        if not effective_client:
            return self._mock_result(inputs)

        # KB-09 — forced/hybrid pre-injection. When a retriever is wired AND
        # this agent's retrieval mode pre-injects, pull the top-K relevant
        # chunks for the task and stash them; _build_system_prompt then grounds
        # the agent in ranked retrieval INSTEAD of the wholesale lessons dump
        # (FR-005). Soft-fails to "" → wholesale lessons fallback, so when the
        # KB is unavailable (no retriever) behaviour is byte-for-byte unchanged.
        rc = retrieval_config or {}
        mode = str(rc.get("mode", "none"))
        if kb_retriever is not None and kb_scope is not None and mode in ("forced", "hybrid"):
            await self._inject_forced_grounding(inputs, kb_retriever, kb_scope, rc)

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
                # PAM-06: pass the per-call client + model as kwargs so
                # the LLM call doesn't read self.* (concurrency fix).
                llm_client=effective_client,
                model=effective_model,
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
                model=effective_model,
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
            model=effective_model,
        )

    async def single_call(
        self,
        prompt: str,
        max_tokens: int | None = None,
        *,
        llm_client: Any | None = None,
        model: str | None = None,
        tool_calling_mode: str | None = None,
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

        PAM-07: ``llm_client``, ``model``, and ``tool_calling_mode`` are
        keyword arguments threaded from the executor's ModelResolver.
        Defaults to instance attrs for legacy callers (PDB-05 BPD path
        still calls ``executor.single_agent_call`` which now passes
        these kwargs).

        Returns a stub result in mock mode (no LLM client configured) so
        dev environments still exercise the wire-up.
        """
        effective_client = llm_client if llm_client is not None else self._llm_client
        effective_model = model if model is not None else self.model
        if not effective_client:
            return {
                "text": f"(mock {self.agent_id} output for prompt: {prompt[:80]}...)",
                "input_tokens": 0,
                "output_tokens": 0,
                "model": effective_model,
            }
        response = await self._call_anthropic(
            messages=[{"role": "user", "content": prompt}],
            tool_schemas=[],
            max_tokens=max_tokens,
            llm_client=effective_client,
            model=effective_model,
        )
        return {
            "text": response.get("text", ""),
            "input_tokens": response.get("input_tokens", 0),
            "output_tokens": response.get("output_tokens", 0),
            "model": effective_model,
            # Surfaced so callers can detect truncation (stop_reason ==
            # "max_tokens" means the response was cut off — used by BPD
            # generation endpoints to show a "split this epic" hint per
            # BPD-18 instead of silently returning a malformed JSON).
            "stop_reason": response.get("stop_reason"),
        }

    def _build_result(
        self, text: str, llm_calls: int, tool_calls: int,
        input_tokens: int, output_tokens: int,
        *, model: str | None = None,
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
            # PAM-06: prefer per-call model (threaded as kwarg) so concurrent
            # calls with different models report accurately. Falls back to
            # self.model for legacy callers.
            "model": model if model is not None else self.model,
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
        # KB-09: prefer ranked KB grounding (forced/hybrid retrieval) when it
        # was computed this call; otherwise fall back to the wholesale lessons
        # dump. The ranked block includes the relevant lessons (the lessons doc
        # is in kb_platform), so FR-005 holds when the KB is up, and the
        # fallback preserves today's behaviour when it's down.
        grounding = getattr(self, "_kb_grounding", "")
        knowledge_block = grounding or self._load_cross_agent_lessons()
        return date_header + knowledge_block + self.system_prompt

    async def _inject_forced_grounding(
        self, inputs: dict[str, Any], retriever: Any, kb_scope: Any, rc: dict[str, Any]
    ) -> None:
        """Retrieve top-K relevant chunks for the task and stash a grounding
        block on ``self._kb_grounding``. Never raises — retrieval failure just
        leaves grounding empty (→ wholesale-lessons fallback)."""
        # Self-contained default so the attribute always exists after this
        # method runs, even if called outside process_task (which also sets it).
        self._kb_grounding = ""
        try:
            query = self._kb_query_from_inputs(inputs)
            if not query:
                return
            top_k = int(rc.get("forced_top_k", 5))
            req_id = getattr(kb_scope, "request_id", None)
            # FACTS — the citeable scope (project namespace for a project task).
            hits = await retriever.retrieve(
                query, kb_scope.namespace,
                bucket_ids=getattr(kb_scope, "bucket_ids", None) or None,
                agent_id=self.agent_id, request_id=req_id, top_k=top_k,
            )
            # CRAFT (KB-17) — optional secondary scope (platform). Retrieved to
            # inform format/method/tone but NEVER citeable as a substantive fact
            # (§5.1). No bucket filter — craft is platform-wide.
            craft_ns = getattr(kb_scope, "craft_namespace", None)
            craft_hits: list[Any] = []
            if craft_ns and craft_ns != kb_scope.namespace:
                craft_hits = await retriever.retrieve(
                    query, craft_ns, bucket_ids=None,
                    agent_id=self.agent_id, request_id=req_id,
                    top_k=int(rc.get("craft_top_k", 3)),
                )
            # KB-19 cold-start — a project task whose app KB has no grounded
            # facts yet. Emit an explicit SPARSE banner so the agent leans on
            # the task brief/PRD + provided sources and flags ungrounded claims
            # (rather than silently falling back to platform craft as if it
            # were app fact).
            sparse = getattr(kb_scope, "is_project", False) and not hits
            self._kb_grounding = self._format_grounding(hits, craft_hits, sparse=sparse)
        except Exception as e:  # noqa: BLE001 — never block the agent on retrieval
            logger.warning("kb_forced_injection_failed", agent=self.agent_id, err=str(e))

    @staticmethod
    def _kb_query_from_inputs(inputs: dict[str, Any]) -> str:
        """Build a retrieval query from the task inputs. Uses the most
        descriptive text field (description / requirements / the first long
        string), capped so the embed call stays cheap."""
        for key in ("description", "requirements", "task", "content", "title"):
            v = inputs.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:1000]
        for v in inputs.values():
            if isinstance(v, str) and len(v.strip()) > 20:
                return v.strip()[:1000]
        return ""

    @staticmethod
    def _format_grounding(
        hits: list[Any], craft_hits: list[Any] | None = None, sparse: bool = False,
    ) -> str:
        """Wrap retrieved chunks as a system-prompt grounding block.

        Two sections (KB-17): FACTS are citeable as ``[KB#id]``; CRAFT
        (platform conventions/format/method) is guidance only and must NEVER be
        cited as a substantive fact (§5.1). ``sparse`` (KB-19) prepends a
        cold-start banner for a project whose app KB has no grounded facts yet.
        Empty facts + empty craft + not sparse → "" (the caller then falls back
        to wholesale lessons)."""
        craft_hits = craft_hits or []
        if not hits and not craft_hits and not sparse:
            return ""
        parts: list[str] = []
        if sparse:
            parts += [
                "=== APP KNOWLEDGE SPARSE ===",
                "This application's knowledge base has little or no grounded content",
                "yet. Ground substantive claims in the task's PRD/brief and any",
                "provided sources. If you cannot ground a claim, FLAG it and lower",
                "your stated confidence — do NOT invent app-specific facts.\n",
            ]
        if hits:
            parts += [
                "=== RELEVANT KNOWLEDGE (retrieved for this task — cite as [KB#id]) ===",
                "These are the grounded FACTS for this task. Cite the source of every",
                "substantive claim as [KB#id]. If a claim isn't supported here, say so",
                "rather than inventing it (citation-or-flag).\n",
            ]
            for h in hits:
                src = getattr(h, "title", "") or getattr(h, "doc_id", "")
                parts.append(f"[KB#{h.chunk_id}] ({src})\n{h.text}\n")
            parts.append("=== END KNOWLEDGE ===\n")
        if craft_hits:
            parts += [
                "=== PLATFORM CRAFT (format / method / tone — GUIDANCE ONLY) ===",
                "Use these for HOW to do the work (structure, conventions, style).",
                "They are NOT facts about this application — do NOT cite them as",
                "substantive claims.\n",
            ]
            for h in craft_hits:
                src = getattr(h, "title", "") or getattr(h, "doc_id", "")
                parts.append(f"(craft: {src})\n{h.text}\n")
            parts.append("=== END CRAFT ===\n")
        return "\n".join(parts)

    # Agents that consume cross-agent lessons. Other agents (PRD,
    # user_story, research, content) don't write code so the lessons
    # would be noise in their context budget — keep their prompt lean.
    _LESSONS_CONSUMER_AGENTS: frozenset[str] = frozenset({
        "backend_specialist",
        "frontend_specialist",
        "code_reviewer",
        "tester_specialist",
        "devops_specialist",
        "architecture_reviewer",
        "security_specialist",
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
            # budget. Was 30KB; raised to 50KB on 2026-05-22 after L17
            # tipped the doc to 33KB and started truncating the
            # maintenance log. 50KB ≈ 13K tokens — still <7% of Opus
            # 4.7's 200K context. Catches runaway edits without
            # silently dropping recent lessons.
            if len(text) > 50_000:
                text = text[:50_000] + "\n\n[... truncated; trim docs/agent-lessons-learned.md]"
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
        *,
        llm_client: Any | None = None,
        model: str | None = None,
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

        # PAM-06: resolve client + model from per-call kwargs, falling back to
        # instance attrs for legacy callers (single_call still uses self.*).
        effective_client = llm_client if llm_client is not None else self._llm_client
        effective_model = model if model is not None else self.model
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
                    "model": effective_model,
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
                        self._stream_messages(kwargs, llm_client=effective_client),
                        timeout=timeout_seconds,
                    )
                else:
                    # Wrap the LLM call in asyncio.wait_for so a hung connection
                    # raises TimeoutError after _LLM_CALL_TIMEOUT_SECONDS instead of
                    # blocking the workflow indefinitely.
                    response = await _asyncio.wait_for(
                        effective_client.messages.create(**kwargs),
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

    async def _stream_messages(
        self, kwargs: dict[str, Any],
        *, llm_client: Any | None = None,
    ) -> Any:
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
        # PAM-06: prefer the per-call client; fall back to instance attr.
        client = llm_client if llm_client is not None else self._llm_client
        async with client.messages.stream(**kwargs) as stream:
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
                # KB-09 — forward the knowledge grounding scope so
                # knowledge_search/get retrieve only within the Request's
                # buckets. The agent never sets this; the executor injected it.
                kb_scope=getattr(self, "_current_kb_scope", None),
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
