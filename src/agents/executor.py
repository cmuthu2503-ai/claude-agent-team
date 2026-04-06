"""Agent executor — bridges the agent system with the orchestrator."""

import os
from typing import Any

import anthropic
import structlog

from src.agents.factory import AgentFactory
from src.agents.registry import AgentRegistry
from src.config.loader import ConfigLoader
from src.tools.registry import ToolRegistry

logger = structlog.get_logger()


class AgentSystemExecutor:
    """Executes agent tasks using real LLM calls via the Anthropic SDK."""

    def __init__(self, config: ConfigLoader) -> None:
        self.config = config
        self.registry = AgentRegistry()
        self.tool_registry = ToolRegistry(config)

        # Create Anthropic client
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key or api_key.startswith("sk-ant-xxxxx"):
            logger.warning("no_api_key", message="ANTHROPIC_API_KEY not set, agents will use mock mode")
            self.client = None
        else:
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
            logger.info("anthropic_client_initialized")

        # Register tool implementations
        from src.tools.file_tools import FileReadTool, FileWriteTool
        from src.tools.git_tools import GitTool
        from src.tools.code_tools import CodeExecTool, TestRunnerTool, CodeAnalysisTool
        from src.tools.github_tools import GitHubAPITool, GitHubPRReviewTool

        self.tool_registry.register_implementation("file_read", FileReadTool())
        self.tool_registry.register_implementation("file_write", FileWriteTool())
        self.tool_registry.register_implementation("git_operations", GitTool())
        self.tool_registry.register_implementation("code_exec", CodeExecTool())
        self.tool_registry.register_implementation("test_runner", TestRunnerTool())
        self.tool_registry.register_implementation("code_analysis", CodeAnalysisTool())
        self.tool_registry.register_implementation("github_api", GitHubAPITool())
        self.tool_registry.register_implementation("github_pr_review", GitHubPRReviewTool())

        # Create all agents
        factory = AgentFactory(config)
        agents = factory.create_all()
        self.registry.register_all(agents)

        # Inject LLM client and tool registry into each agent
        for agent in agents.values():
            if self.client:
                agent.set_llm_client(self.client)
            agent.set_tool_registry(self.tool_registry)

        logger.info("agent_system_ready", agents=len(agents), has_llm=self.client is not None, tools=len(self.tool_registry.list_tools()))

    async def execute(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute an agent task with real LLM calls."""
        agent = self.registry.get(agent_id)
        if not agent:
            logger.error("agent_not_found", agent_id=agent_id)
            return {"status": "failed", "error": f"Agent '{agent_id}' not found", "outputs": {}, "artifacts": []}

        logger.info(
            "agent_executing",
            agent_id=agent_id, request_id=request_id,
            model=agent.model, has_llm=agent._llm_client is not None,
        )

        result = await agent.process_task(request_id, inputs)
        return result
