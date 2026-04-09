"""Agent executor — bridges the agent system with the orchestrator.

Supports five LLM providers (plus back-compat aliases):
  - "anthropic_opus":   direct Anthropic API, all agents forced to Claude Opus 4.6
  - "anthropic_sonnet": direct Anthropic API, all agents forced to Claude Sonnet 4.6
  - "bedrock":          Amazon Bedrock, all agents forced to Claude Sonnet 4
  - "openai_gpt5":      OpenAI Chat Completions API, all agents on GPT-5
  - "openai_o3":        OpenAI Chat Completions API, all agents on o3 (reasoning)

Legacy alias:
  - "anthropic":        maps to "anthropic_sonnet" (per-agent YAML model behavior is
                        no longer exposed in the UI but we still accept this value
                        on persisted rows — it resolves to the direct Anthropic
                        client with each agent's YAML-configured model).
"""

import os
from typing import Any

import anthropic
import structlog

from src.agents.factory import AgentFactory
from src.agents.registry import AgentRegistry
from src.config.loader import ConfigLoader
from src.tools.registry import ToolRegistry
from src.utils.secrets import read_secret

logger = structlog.get_logger()

# ── Model IDs (all overridable via env) ─────────────
# Bedrock: Claude Sonnet 4 for ALL agents in bedrock mode
BEDROCK_SONNET_4_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-sonnet-4-20250514-v1:0",
)
# Anthropic direct: forced model IDs when opus/sonnet buttons are used
ANTHROPIC_OPUS_MODEL_ID = os.getenv("ANTHROPIC_OPUS_MODEL_ID", "claude-opus-4-6")
ANTHROPIC_SONNET_MODEL_ID = os.getenv("ANTHROPIC_SONNET_MODEL_ID", "claude-sonnet-4-6")
# OpenAI: forced model IDs when the GPT / reasoning buttons are used.
# Defaults point at the latest models visible on OpenAI's API (as of 2026-04-08):
#   gpt-5.4   — latest GPT-5 flagship (released 2026-03-05)
#   o4-mini   — latest o-series reasoning model (released 2025-04-16)
# Both are env-overridable so you can pin a specific date-stamped variant, switch
# to gpt-5.4-pro, or roll back to plain 'gpt-5' / 'o3' without touching code.
OPENAI_GPT5_MODEL_ID = os.getenv("OPENAI_GPT5_MODEL_ID", "gpt-5.4")
OPENAI_O3_MODEL_ID = os.getenv("OPENAI_O3_MODEL_ID", "o4-mini")

# Known-valid provider strings (accepted by the API layer)
VALID_PROVIDERS: frozenset[str] = frozenset({
    "anthropic",          # legacy alias — resolves to anthropic_sonnet
    "anthropic_opus",
    "anthropic_sonnet",
    "bedrock",
    "openai_gpt5",
    "openai_o3",
})


class AgentSystemExecutor:
    """Executes agent tasks via Anthropic direct, Amazon Bedrock, or OpenAI."""

    def __init__(self, config: ConfigLoader) -> None:
        self.config = config
        self.registry = AgentRegistry()
        self.tool_registry = ToolRegistry(config)

        # ── Anthropic direct client ──────────────────
        api_key = read_secret("anthropic_api_key", "ANTHROPIC_API_KEY")
        if not api_key or api_key.startswith("sk-ant-xxxxx"):
            self.anthropic_client: Any = None
            logger.warning("no_anthropic_api_key", message="ANTHROPIC_API_KEY not set")
        else:
            self.anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
            logger.info("anthropic_client_initialized")

        # ── Bedrock client ───────────────────────────
        # AsyncAnthropicBedrock reads AWS creds from boto3's standard credential chain
        # (env vars), so when secrets-as-files are used we need to populate the env
        # vars from the secret file before instantiating the client.
        aws_region = os.getenv("AWS_REGION", "us-east-1")
        aws_access_key = read_secret("aws_access_key_id", "AWS_ACCESS_KEY_ID")
        aws_secret_key = read_secret("aws_secret_access_key", "AWS_SECRET_ACCESS_KEY")
        if aws_access_key and not os.environ.get("AWS_ACCESS_KEY_ID"):
            os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
        if aws_secret_key and not os.environ.get("AWS_SECRET_ACCESS_KEY"):
            os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key
        has_aws_creds = bool(aws_access_key) and bool(aws_secret_key)
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

        # ── OpenAI client ────────────────────────────
        openai_key = read_secret("openai_api_key", "OPENAI_API_KEY")
        if not openai_key or openai_key.startswith("sk-xxxxx"):
            self.openai_client: Any = None
            logger.info("openai_disabled", reason="OPENAI_API_KEY not set")
        else:
            try:
                from openai import AsyncOpenAI  # local import so openai stays optional
                self.openai_client = AsyncOpenAI(api_key=openai_key)
                logger.info(
                    "openai_client_initialized",
                    gpt5_model=OPENAI_GPT5_MODEL_ID,
                    o3_model=OPENAI_O3_MODEL_ID,
                )
            except Exception as e:
                self.openai_client = None
                logger.warning("openai_client_init_failed", error=str(e))

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
            openai_available=self.openai_client is not None,
            tools=len(self.tool_registry.list_tools()),
        )

    def _resolve_provider(self, provider: str) -> tuple[Any, str | None]:
        """Pick the (client, model_override) tuple for the requested provider.

        Returns (None, None) if no client is available — caller falls back to mock.
        All non-legacy providers force a single model for EVERY agent. The legacy
        'anthropic' alias falls back to each agent's YAML-configured model.
        """
        if provider == "anthropic_opus":
            if not self.anthropic_client:
                raise RuntimeError(
                    "Claude Opus provider requested but ANTHROPIC_API_KEY is not configured."
                )
            return self.anthropic_client, ANTHROPIC_OPUS_MODEL_ID

        if provider == "anthropic_sonnet":
            if not self.anthropic_client:
                raise RuntimeError(
                    "Claude Sonnet provider requested but ANTHROPIC_API_KEY is not configured."
                )
            return self.anthropic_client, ANTHROPIC_SONNET_MODEL_ID

        if provider == "bedrock":
            if not self.bedrock_client:
                raise RuntimeError(
                    "Bedrock provider requested but AWS credentials are not configured. "
                    "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION."
                )
            return self.bedrock_client, BEDROCK_SONNET_4_MODEL_ID

        if provider == "openai_gpt5":
            if not self.openai_client:
                raise RuntimeError(
                    "OpenAI provider requested but OPENAI_API_KEY is not configured."
                )
            return self.openai_client, OPENAI_GPT5_MODEL_ID

        if provider == "openai_o3":
            if not self.openai_client:
                raise RuntimeError(
                    "OpenAI provider requested but OPENAI_API_KEY is not configured."
                )
            return self.openai_client, OPENAI_O3_MODEL_ID

        # Legacy 'anthropic' alias — use per-agent YAML model with the direct client
        return self.anthropic_client, None

    async def execute(
        self, agent_id: str, request_id: str, inputs: dict[str, Any],
        provider: str = "anthropic_sonnet",
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
