"""Agent executor — bridges the agent system with the orchestrator.

Single LLM provider: Claude Platform on AWS (Anthropic-operated, AWS-authenticated).
All agents share one AnthropicAWS client and the same model — set in
`config/agents/*.yaml` as `model:` (e.g. `claude-opus-4-7`).

Not Bedrock. Not the direct Anthropic API. See docs/setup-claude-platform-on-aws.md.
"""

import os
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

        if not self.anthropic_client:
            agent.set_llm_client(None)
            logger.info("agent_executing_mock", agent_id=agent_id, request_id=request_id)
            return await agent.process_task(request_id, inputs)

        logger.info(
            "agent_executing",
            agent_id=agent_id, request_id=request_id, model=agent.model,
            inference_geo=self.inference_geo or "global",
        )
        return await agent.process_task(request_id, inputs)

    async def single_agent_call(
        self,
        agent_id: str,
        prompt: str,
        project_artifact_id: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One-shot agent call for Project-driven Build (PDB-05).

        Unlike `execute()`, this:
        - does NOT create a Request, Subtask, or emit `request.*` events;
        - does NOT run the tool-use loop (no file_read, git, etc.);
        - records token usage attributed to `project_artifact_id` rather
          than a request_id, so the cost dashboard can scope per-project
          spend across both Requests and artifact-generation calls.

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
            max_tokens=max_tokens,
            inference_geo=self.inference_geo or "global",
        )
        result = await agent.single_call(prompt, max_tokens=max_tokens)

        # Persist token usage so the cost dashboard's project filter picks
        # this call up. In mock mode (no client → both counts 0) we still
        # record the row so the wiring is observable.
        if self.state is not None:
            import uuid as _uuid
            from src.models.base import TokenUsage as _TokenUsage
            try:
                await self.state.record_token_usage(_TokenUsage(
                    usage_id=f"usage-{_uuid.uuid4().hex[:12]}",
                    request_id="",
                    subtask_id="",
                    agent_id=agent_id,
                    model=str(result.get("model") or agent.model or ""),
                    input_tokens=int(result.get("input_tokens") or 0),
                    output_tokens=int(result.get("output_tokens") or 0),
                    cost_usd=0.0,  # pricing lookup is out of scope here; supervisor cost reports already cover this
                    project_artifact_id=project_artifact_id,
                ))
            except Exception as e:
                logger.warning("token_usage_record_failed", error=str(e), agent=agent_id)

        return result
