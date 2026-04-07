"""Agent executor — bridges the agent system with the orchestrator.

Supports two LLM providers:
  - "anthropic": direct Anthropic API (per-agent model from YAML)
  - "bedrock":   Amazon Bedrock (all agents use Claude Sonnet 4)
"""

import os
from typing import Any

import anthropic
import structlog

from src.agents.factory import AgentFactory
from src.agents.registry import AgentRegistry
from src.config.loader import ConfigLoader
from src.tools.registry import ToolRegistry

logger = structlog.get_logger()

# Bedrock model ID for Claude Sonnet 4 — used for ALL agents in bedrock mode
BEDROCK_SONNET_4_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-sonnet-4-20250514-v1:0",
)


class AgentSystemExecutor:
    """Executes agent tasks using real LLM calls via Anthropic SDK or Amazon Bedrock."""

    def __init__(self, config: ConfigLoader) -> None:
        self.config = config
        self.registry = AgentRegistry()
        self.tool_registry = ToolRegistry(config)

        # ── Anthropic direct client ──────────────────
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key or api_key.startswith("sk-ant-xxxxx"):
            self.anthropic_client: Any = None
            logger.warning("no_anthropic_api_key", message="ANTHROPIC_API_KEY not set")
        else:
            self.anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
            logger.info("anthropic_client_initialized")

        # ── Bedrock client ───────────────────────────
        # AsyncAnthropicBedrock reads AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION
        # from the environment via boto3's standard credential chain.
        aws_region = os.getenv("AWS_REGION", "us-east-1")
        has_aws_creds = bool(os.getenv("AWS_ACCESS_KEY_ID")) and bool(os.getenv("AWS_SECRET_ACCESS_KEY"))
        if has_aws_creds:
            try:
                self.bedrock_client: Any = anthropic.AsyncAnthropicBedrock(aws_region=aws_region)
                logger.info("bedrock_client_initialized", region=aws_region, model=BEDROCK_SONNET_4_MODEL_ID)
            except Exception as e:
                self.bedrock_client = None
                logger.warning("bedrock_client_init_failed", error=str(e))
        else:
            self.bedrock_client = None
            logger.info("bedrock_disabled", reason="AWS credentials not set")

        # Backward compat: some legacy code may read self.client
        self.client = self.anthropic_client

        # ── Tool implementations ─────────────────────
        from src.tools.file_tools import FileReadTool, FileWriteTool
        from src.tools.git_tools import GitTool
        from src.tools.code_tools import CodeExecTool, TestRunnerTool, CodeAnalysisTool
        from src.tools.github_tools import GitHubAPITool, GitHubPRReviewTool
        from src.tools.firecrawl_tools import WebSearchTool, WebScrapeTool

        self.tool_registry.register_implementation("file_read", FileReadTool())
        self.tool_registry.register_implementation("file_write", FileWriteTool())
        self.tool_registry.register_implementation("git_operations", GitTool())
        self.tool_registry.register_implementation("code_exec", CodeExecTool())
        self.tool_registry.register_implementation("test_runner", TestRunnerTool())
        self.tool_registry.register_implementation("code_analysis", CodeAnalysisTool())
        self.tool_registry.register_implementation("github_api", GitHubAPITool())
        self.tool_registry.register_implementation("github_pr_review", GitHubPRReviewTool())
        self.tool_registry.register_implementation("web_search", WebSearchTool())
        self.tool_registry.register_implementation("web_scrape", WebScrapeTool())

        # ── Create agents ────────────────────────────
        factory = AgentFactory(config)
        agents = factory.create_all()
        self.registry.register_all(agents)

        # Default-inject the Anthropic client (per-call code may swap it out)
        for agent in agents.values():
            if self.anthropic_client:
                agent.set_llm_client(self.anthropic_client)
            agent.set_tool_registry(self.tool_registry)

        logger.info(
            "agent_system_ready",
            agents=len(agents),
            anthropic_available=self.anthropic_client is not None,
            bedrock_available=self.bedrock_client is not None,
            tools=len(self.tool_registry.list_tools()),
        )

    def _resolve_provider(self, provider: str) -> tuple[Any, str | None]:
        """Pick the (client, model_override) tuple for the requested provider.

        Returns (None, None) if no client is available — caller falls back to mock.
        For 'bedrock', model_override forces all agents to Sonnet 4 on Bedrock.
        For 'anthropic', model_override is None (agents use their YAML model).
        """
        if provider == "bedrock":
            if not self.bedrock_client:
                raise RuntimeError(
                    "Bedrock provider requested but AWS credentials are not configured. "
                    "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION."
                )
            return self.bedrock_client, BEDROCK_SONNET_4_MODEL_ID
        # default: anthropic
        return self.anthropic_client, None

    async def execute(
        self, agent_id: str, request_id: str, inputs: dict[str, Any],
        provider: str = "anthropic",
    ) -> dict[str, Any]:
        """Execute an agent task with real LLM calls.

        Per-call provider override: the agent's _llm_client and model are temporarily
        swapped for this single execution, then restored.
        """
        agent = self.registry.get(agent_id)
        if not agent:
            logger.error("agent_not_found", agent_id=agent_id)
            return {"status": "failed", "error": f"Agent '{agent_id}' not found", "outputs": {}, "artifacts": []}

        client, model_override = self._resolve_provider(provider)
        if not client:
            # Fall back to mock — process_task() handles _llm_client=None
            agent.set_llm_client(None)
            logger.info("agent_executing_mock", agent_id=agent_id, request_id=request_id, provider=provider)
            return await agent.process_task(request_id, inputs)

        # Snapshot original state, swap in for this call, restore after.
        original_client = agent._llm_client
        original_model = agent.model
        try:
            agent.set_llm_client(client)
            if model_override:
                agent.model = model_override
            logger.info(
                "agent_executing",
                agent_id=agent_id, request_id=request_id,
                provider=provider, model=agent.model,
            )
            result = await agent.process_task(request_id, inputs)
            return result
        finally:
            agent.set_llm_client(original_client)
            agent.model = original_model
