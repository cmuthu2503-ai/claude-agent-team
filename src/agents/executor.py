"""Agent executor — bridges the agent system with the orchestrator.

Single LLM provider: Claude Platform on AWS (Anthropic-operated, AWS-authenticated).
All agents share one AnthropicAWS client and the same model — set in
`config/agents/*.yaml` as `model:` (e.g. `claude-opus-4-7`).

Not Bedrock. Not the direct Anthropic API. See docs/setup-claude-platform-on-aws.md.
"""

import os
import time
from typing import Any

import structlog

from src.agents.factory import AgentFactory
from src.agents.registry import AgentRegistry
from src.config.loader import ConfigLoader
from src.tools.registry import ToolRegistry
from src.utils.secrets import read_secret

logger = structlog.get_logger()


def _resolve_inference_geo(config: ConfigLoader) -> str | None:
    """Resolve the default `inference_geo` request parameter.

    Source order: env var → project.yaml `llm.inference_geo` → None (global).
    Set to "us" to pin inference to US data centers (1.1x pricing multiplier).
    """
    env_value = os.getenv("ANTHROPIC_AWS_INFERENCE_GEO", "").strip().lower()
    if env_value:
        return env_value
    try:
        if not hasattr(config, "project"):
            return None
        llm_cfg = config.project.get("project", {}).get("llm", {})
        cfg_value = (llm_cfg.get("inference_geo") or "").strip().lower()
        return cfg_value or None
    except Exception:
        return None


class AgentSystemExecutor:
    """Executes agent tasks via Claude Platform on AWS (AnthropicAWS client)."""

    def __init__(self, config: ConfigLoader, state: Any = None) -> None:
        self.config = config
        # State threaded in so tools that need to read deployment_states (e.g.
        # wait_for_deployment) can use the existing async store rather than
        # opening their own SQLite connection. Optional for backward compat
        # with tests that construct the executor without a state store.
        self.state = state
        self.registry = AgentRegistry()
        self.tool_registry = ToolRegistry(config)
        self.inference_geo: str | None = _resolve_inference_geo(config)

        # Token tracker for single_agent_call cost recording. The workflow
        # runner has its own tracker on the orchestrator (records via
        # `_token_tracker.record(...)`), but `single_agent_call` writes
        # directly to `record_token_usage` for the project-artifact path
        # (PDB-05 PRD/tasks gen, BPD 3-pass generators). Both paths now
        # share the same pricing source so the cost dashboard / ticker
        # reports actual USD spend instead of $0.00 for BPD activity.
        self._token_tracker: Any = None
        if state is not None:
            try:
                from src.core.token_tracker import TokenTracker
                self._token_tracker = TokenTracker(state)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "agent_executor_token_tracker_init_failed", error=str(e),
                )

        # In-flight tracking for single_agent_call invocations. The
        # Team Status page (and the cyberpunk overlay's [NET] counter)
        # determine "in progress" from `state.get_active_subtasks()`,
        # but single_agent_call deliberately doesn't create subtasks —
        # so PRD / API spec / epic / feature / task generation flowed
        # through the LLM for 30-90s with every agent card showing
        # "idle". This dict lets /agents merge in the busy set so
        # those calls are visible while running.
        #
        # Keyed by agent_id; value carries `label` (what the agent is
        # doing) and `started_at` (epoch seconds, for elapsed display).
        # An agent_id is in this dict iff exactly one single_agent_call
        # is currently in flight for it — concurrent calls for the same
        # agent would overwrite, which is fine for status display since
        # the user only sees "this agent is busy."
        self._busy_agents: dict[str, dict[str, Any]] = {}

        # ── AnthropicAWS client (Claude Platform on AWS) ─────────────────
        # The SDK reads:
        #   - ANTHROPIC_AWS_API_KEY      (long-term API key from AWS console)
        #   - ANTHROPIC_AWS_WORKSPACE_ID (wrkspc_... from the Workspaces page)
        #   - AWS_REGION (or AWS_DEFAULT_REGION)
        # We push secret-file values into os.environ before instantiation so the
        # SDK picks them up uniformly regardless of dev/staging/prod mode.
        api_key = read_secret("anthropic_aws_api_key", "ANTHROPIC_AWS_API_KEY")
        workspace_id = read_secret("anthropic_aws_workspace_id", "ANTHROPIC_AWS_WORKSPACE_ID")
        aws_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"

        if api_key and not os.environ.get("ANTHROPIC_AWS_API_KEY"):
            os.environ["ANTHROPIC_AWS_API_KEY"] = api_key
        if workspace_id and not os.environ.get("ANTHROPIC_AWS_WORKSPACE_ID"):
            os.environ["ANTHROPIC_AWS_WORKSPACE_ID"] = workspace_id
        os.environ.setdefault("AWS_REGION", aws_region)

        self.anthropic_client: Any = None
        if not api_key:
            logger.warning(
                "no_anthropic_aws_api_key",
                message="ANTHROPIC_AWS_API_KEY not set — falling back to mock execution",
            )
        elif not workspace_id:
            logger.warning(
                "no_anthropic_aws_workspace_id",
                message="ANTHROPIC_AWS_WORKSPACE_ID not set — falling back to mock execution",
            )
        else:
            try:
                from anthropic import AsyncAnthropicAWS  # type: ignore[attr-defined]
                self.anthropic_client = AsyncAnthropicAWS(
                    api_key=api_key,
                    workspace_id=workspace_id,
                    aws_region=aws_region,
                )
                logger.info(
                    "anthropic_aws_client_initialized",
                    region=aws_region,
                    inference_geo=self.inference_geo or "global",
                )
            except Exception as e:
                logger.error("anthropic_aws_client_init_failed", error=str(e))
                self.anthropic_client = None

        # Backward compat: legacy callers still read self.client
        self.client = self.anthropic_client

        # ── Tool implementations ─────────────────────
        from src.tools.file_tools import FileReadTool, FileWriteTool, SearchReplaceTool
        from src.tools.git_tools import GitTool
        from src.tools.code_tools import CodeExecTool, TestRunnerTool, CodeAnalysisTool
        from src.tools.github_tools import GitHubAPITool, GitHubPRReviewTool
        from src.tools.firecrawl_tools import WebSearchTool, WebScrapeTool
        from src.tools.deploy_tools import WaitForDeploymentTool
        from src.tools.lessons_writer import LessonsWriterTool
        from src.tools.security_scan import SecurityScanTool
        from src.tools.ops_check import OpsCheckTool
        from src.tools.policy_check import PolicyCheckTool

        self.tool_registry.register_implementation("file_read", FileReadTool())
        self.tool_registry.register_implementation("file_write", FileWriteTool())
        # search_replace gives code-producing agents a surgical-edit primitive
        # so they don't hit response-length truncation when modifying files
        # >470 lines (the root cause of REQ-7F2E07's 3-cycle failure loop).
        self.tool_registry.register_implementation("search_replace", SearchReplaceTool())
        self.tool_registry.register_implementation("git_operations", GitTool())
        self.tool_registry.register_implementation("code_exec", CodeExecTool())
        self.tool_registry.register_implementation("test_runner", TestRunnerTool())
        self.tool_registry.register_implementation("code_analysis", CodeAnalysisTool())
        self.tool_registry.register_implementation("github_api", GitHubAPITool())
        self.tool_registry.register_implementation("github_pr_review", GitHubPRReviewTool())
        self.tool_registry.register_implementation("web_search", WebSearchTool())
        self.tool_registry.register_implementation("web_scrape", WebScrapeTool())
        # wait_for_deployment lets devops_specialist observe the supervisor's real
        # deployment outcome (judge decision + step_history + final status) instead
        # of producing a fictional report. Requires state for SQLite access.
        self.tool_registry.register_implementation(
            "wait_for_deployment", WaitForDeploymentTool(state=self.state),
        )
        # lessons_writer is the write side of the self-learning loop.
        # The self_learning_agent uses this to append new failure-pattern
        # lessons to docs/agent-lessons-learned.md after a Request fails.
        self.tool_registry.register_implementation("lessons_writer", LessonsWriterTool())
        # security_scan is the scanning back-end for the security_specialist agent.
        # Wraps bandit, safety, npm audit, and detect-secrets with graceful SKIP
        # fallback when any binary is absent from the environment.
        self.tool_registry.register_implementation("security_scan", SecurityScanTool())
        # ops_check is the health-monitoring back-end for the ops_heal_agent.
        # Checks HTTP health endpoints, disk/memory pressure, and recent error
        # log patterns after each deployment.
        self.tool_registry.register_implementation("ops_check", OpsCheckTool())
        # policy_check evaluates the declarative rule catalog
        # (config/quality-rules.yaml) against agent emissions for the
        # quality_guardian agent. The tool's constructor loads + validates
        # the YAML eagerly — if rules are malformed it raises here.
        # Catching the failure rather than letting it crash backend boot:
        # a bad rules file should disable quality_guardian, not the whole
        # platform. Operator sees the WARNING in logs, fixes the YAML,
        # restarts. quality_guardian will fail at request-time with
        # "tool not registered" until then — also loud, but scoped.
        try:
            self.tool_registry.register_implementation("policy_check", PolicyCheckTool())
        except Exception as e:  # noqa: BLE001
            logger.error(
                "policy_check_registration_failed",
                error=str(e),
                hint=(
                    "config/quality-rules.yaml failed to load or validate. "
                    "quality_guardian will be unable to call policy_check until "
                    "the YAML is fixed and the backend restarted. See "
                    "docs/quality-rules-schema.md for the expected schema."
                ),
            )

        # ── Create agents ────────────────────────────
        factory = AgentFactory(config)
        agents = factory.create_all()
        self.registry.register_all(agents)

        for agent in agents.values():
            if self.anthropic_client:
                agent.set_llm_client(self.anthropic_client)
                agent.set_inference_geo(self.inference_geo)
            agent.set_tool_registry(self.tool_registry)

        logger.info(
            "agent_system_ready",
            agents=len(agents),
            llm_available=self.anthropic_client is not None,
            tools=len(self.tool_registry.list_tools()),
        )

    async def _resolve_project_root_for_request(
        self, request_id: str,
    ) -> "Path | None":
        """Look up the request's project (if any) and resolve its
        per-project working tree path.

        Returns None for platform-level Requests (no project_id, or
        project_id == proj-unassigned). When non-None, gets threaded
        through ``process_task`` → ``_execute_tool`` → tool registry →
        FileReadTool / FileWriteTool / SearchReplaceTool's path
        resolver, so filesystem operations land in the per-project
        tree (e.g. ``C:/ai-projects/CrewAI/``) instead of the
        platform's ``/app`` tree.

        Soft-fails (returns None) on any lookup error — better to fall
        back to platform-tree behaviour than to crash an agent
        invocation because of a transient DB issue.
        """
        try:
            from src.core.project_workspace import project_root_dir
            from src.models.base import UNASSIGNED_PROJECT_ID
            request = await self.state.get_request(request_id)
            if not request or not request.project_id:
                return None
            if request.project_id == UNASSIGNED_PROJECT_ID:
                return None
            project = await self.state.get_project(request.project_id)
            if not project:
                return None
            return project_root_dir(project.name)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "agent_executor_project_root_resolve_failed",
                request_id=request_id, error=str(e),
            )
            return None

    async def execute(
        self, agent_id: str, request_id: str, inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an agent task. Falls back to mock if the client isn't configured."""
        agent = self.registry.get(agent_id)
        if not agent:
            logger.error("agent_not_found", agent_id=agent_id)
            return {
                "status": "failed",
                "error": f"Agent '{agent_id}' not found",
                "outputs": {},
                "artifacts": [],
            }

        # Resolve the per-project working tree ONCE per invocation. None
        # means "platform-level work" (no project_id or
        # proj-unassigned), which preserves the legacy behaviour where
        # filesystem tools resolve under the platform's project_root.
        project_root = await self._resolve_project_root_for_request(request_id)

        if not self.anthropic_client:
            agent.set_llm_client(None)
            logger.info(
                "agent_executing_mock",
                agent_id=agent_id, request_id=request_id,
                project_root=str(project_root) if project_root else "platform",
            )
            return await agent.process_task(
                request_id, inputs, project_root=project_root,
            )

        logger.info(
            "agent_executing",
            agent_id=agent_id, request_id=request_id, model=agent.model,
            inference_geo=self.inference_geo or "global",
            project_root=str(project_root) if project_root else "platform",
        )
        return await agent.process_task(
            request_id, inputs, project_root=project_root,
        )

    def get_busy_agents(self) -> dict[str, dict[str, Any]]:
        """Snapshot of currently-busy agents (single_agent_call in flight).

        Returns a shallow copy so callers can iterate without worrying
        about the dict mutating mid-loop if a call finishes concurrently.
        Each value: {'label': str, 'started_at': float epoch-seconds}.
        Empty dict when nothing's in flight.
        """
        return dict(self._busy_agents)

    async def single_agent_call(
        self,
        agent_id: str,
        prompt: str,
        project_artifact_id: str | None = None,
        max_tokens: int | None = None,
        project_id: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        """One-shot agent call for Project-driven Build (PDB-05).

        Unlike `execute()`, this:
        - does NOT create a Request, Subtask, or emit `request.*` events;
        - does NOT run the tool-use loop (no file_read, git, etc.);
        - records token usage attributed to ``project_artifact_id`` AND
          (preferred) ``project_id`` so the cost dashboard's per-project
          filter is a direct `WHERE project_id = ?` instead of an OR over
          two subqueries. Callers that don't have an artifact_id (the
          BPD epic/feature/task generators) still get correctly attributed
          cost by passing project_id only.

        ``max_tokens`` overrides the default 8192 cap on the underlying
        Messages API call. Long-form generators (PRD, API spec, tasks
        list) pass a higher value (32 000+) so the response doesn't get
        truncated mid-document.

        Returns `{text, input_tokens, output_tokens, model}`. Caller
        persists `text` into the artifact's `content` column.
        """
        agent = self.registry.get(agent_id)
        if not agent:
            logger.error("single_agent_not_found", agent_id=agent_id)
            return {"text": "", "input_tokens": 0, "output_tokens": 0, "model": None, "error": "agent_not_found"}

        logger.info(
            "single_agent_call",
            agent_id=agent_id, artifact_id=project_artifact_id,
            project_id=project_id,
            label=label,
            max_tokens=max_tokens,
            inference_geo=self.inference_geo or "global",
        )
        # Mark the agent as busy for the duration of the LLM call so
        # the Team Status page (5s poll) and the [NET] counter on the
        # cyberpunk overlay show real activity instead of "0 agents
        # active" while a 90-second PRD generation is running.
        # try/finally guarantees we always clear it — even on exception
        # or task cancellation — so a crashed generator can't leave an
        # agent stuck "in progress" forever.
        self._busy_agents[agent_id] = {
            "label": label or f"single_call ({agent_id})",
            "started_at": time.time(),
        }
        try:
            result = await agent.single_call(prompt, max_tokens=max_tokens)
        finally:
            self._busy_agents.pop(agent_id, None)

        # Persist token usage so the cost dashboard's project filter picks
        # this call up. In mock mode (no client → both counts 0) we still
        # record the row so the wiring is observable.
        #
        # Cost is computed via the shared TokenTracker so this path uses
        # the same pricing source as the workflow runner (config/thresholds.yaml
        # ::cost.pricing). Previously cost_usd was hardcoded to 0.0, which
        # caused BPD generation spend (epics/features/tasks 3-pass) plus
        # PDB-05 (PRD/brief/tasks gen) to disappear from the cost dashboard
        # and the cyberpunk overlay's "[COST] today $X" ticker — tokens
        # accumulated correctly but the dollar column stayed zero.
        if self.state is not None:
            import uuid as _uuid
            from src.models.base import TokenUsage as _TokenUsage
            input_tokens = int(result.get("input_tokens") or 0)
            output_tokens = int(result.get("output_tokens") or 0)
            model_name = str(result.get("model") or agent.model or "")
            cost_usd = 0.0
            if self._token_tracker and model_name:
                try:
                    cost_usd = self._token_tracker.calculate_cost(
                        model_name, input_tokens, output_tokens,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "single_agent_cost_calc_failed",
                        error=str(e), agent=agent_id, model=model_name,
                    )
            try:
                await self.state.record_token_usage(_TokenUsage(
                    usage_id=f"usage-{_uuid.uuid4().hex[:12]}",
                    request_id="",
                    subtask_id="",
                    agent_id=agent_id,
                    model=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    project_artifact_id=project_artifact_id,
                    project_id=project_id,
                ))
            except Exception as e:
                logger.warning("token_usage_record_failed", error=str(e), agent=agent_id)

        return result
