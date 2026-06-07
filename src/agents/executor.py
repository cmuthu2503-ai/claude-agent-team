"""Agent executor — bridges the agent system with the orchestrator.

PAM-07: Per-call model resolution
---------------------------------
The executor now resolves the model PER agent invocation through
``ModelResolver`` (PAM-05), which consults the 5-layer precedence chain
(request_override → db_override → YAML default → env default → catalog
default). The resolved ``(client, model, tool_calling_mode)`` triple is
threaded into ``BaseAgent.process_task()`` as keyword arguments (PAM-06)
so concurrent calls on the same agent with different models can't
cross-pollinate via shared instance state.

The single AnthropicAWS client is still constructed eagerly at boot for
the common case and registered into ``LLMClientPool`` (PAM-03) under the
``anthropic_aws`` provider key. Other providers (Bedrock, OpenAI,
Ollama in PR-4) are built lazily by the pool the first time an agent
resolves to one of those models.

See docs/setup-claude-platform-on-aws.md for Claude Platform on AWS
auth setup (the platform's primary provider).
"""

import hashlib
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

        # ── PAM-07 — ModelCatalog + LLMClientPool + ModelResolver ──
        # Catalog is the single source of truth for "which models exist
        # and how to talk to each one." Resolver is the per-call entry
        # point the dispatcher uses to pick a model (5-layer precedence).
        # Client pool caches one client per (provider_type, base_url).
        # All three soft-fail: if config/models.yaml is missing or
        # invalid we log a WARNING and fall back to the legacy
        # ``agent.set_llm_client(self.anthropic_client)`` path so the
        # platform stays bootable even with a broken catalog.
        # KB-09 — the agentic knowledge subsystem (retriever + stores +
        # ingestion). Built asynchronously in main.py's lifespan (KB-10) and
        # set here afterward; None until then. When None/unavailable, agents
        # run without retrieval — behaviour identical to pre-KB.
        self.kb_subsystem: Any = None
        self.model_catalog: Any = None
        self.client_pool: Any = None
        self.model_resolver: Any = None
        try:
            from src.agents.client_pool import LLMClientPool
            from src.agents.model_resolver import ModelResolver
            from src.models.catalog import ModelCatalog, default_catalog_path

            self.model_catalog = ModelCatalog.load(default_catalog_path())
            # LLMClientPool takes no constructor args — it's a stateless
            # cache keyed by (provider_type, base_url). The catalog
            # passes per-model context at get_for() time, not init time.
            self.client_pool = LLMClientPool()
            # Pre-register the already-built AnthropicAWS client so the
            # pool returns the same instance for any anthropic_aws model
            # (no double-construction). Lazy paths still apply for
            # bedrock / openai / openai_compat / ollama.
            if self.anthropic_client and hasattr(
                self.client_pool, "register_prebuilt"
            ):
                try:
                    self.client_pool.register_prebuilt(
                        provider_type="anthropic_aws",
                        client=self.anthropic_client,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "client_pool_prebuilt_registration_failed",
                        error=str(e),
                    )
            self.model_resolver = ModelResolver(
                catalog=self.model_catalog,
                client_pool=self.client_pool,
                agents_config=getattr(config, "agents", None),
                # PAM-11 — wire the state store so layer 2 (DB override)
                # is now live. The resolver tolerates state being None
                # (mock-mode tests, executor constructed without state)
                # and tolerates lookup failures (logs + falls through).
                state_store=self.state,
            )
            # PAM-15: attach the freshly-loaded catalog to the token
            # tracker so cost calc reads pricing from models.yaml
            # rather than the legacy thresholds.yaml block. Set after
            # the catalog is loaded — the tracker was constructed
            # earlier (line above) before catalog existed; back-fill
            # the reference now so the next record() call uses it.
            if self._token_tracker is not None:
                self._token_tracker._catalog = self.model_catalog
            logger.info(
                "model_resolver_initialized",
                catalog_models=len(self.model_catalog.models),
                default_model=self.model_catalog.default_model,
            )
        except Exception as e:  # noqa: BLE001
            # Catalog / pool / resolver init failed — the platform must
            # still boot. The legacy path (eager set_llm_client below)
            # picks up the slack: every agent gets the AnthropicAWS
            # client and its YAML model, exactly as before PAM-07.
            logger.warning(
                "model_resolver_init_failed_falling_back_to_legacy_path",
                error=str(e),
                hint=(
                    "config/models.yaml is missing or invalid. Per-call "
                    "model resolution disabled; all agents will use the "
                    "single AnthropicAWS client + their YAML model. Fix "
                    "the catalog and restart to re-enable PAM-07."
                ),
            )

        # ── Tool implementations ─────────────────────
        from src.tools.anomaly_detect import AnomalyDetectTool
        from src.tools.auto_rollback import AutoRollbackTool
        from src.tools.code_tools import CodeAnalysisTool, CodeExecTool, TestRunnerTool
        from src.tools.dependency_audit import DependencyAuditTool
        from src.tools.deploy_tools import WaitForDeploymentTool
        from src.tools.file_tools import FileReadTool, FileWriteTool, SearchReplaceTool
        from src.tools.firecrawl_tools import WebScrapeTool, WebSearchTool
        from src.tools.git_tools import GitTool
        from src.tools.github_tools import GitHubAPITool, GitHubPRReviewTool
        from src.tools.health_probe import HealthProbeTool
        from src.tools.lessons_writer import LessonsWriterTool
        from src.tools.ops_check import OpsCheckTool
        from src.tools.pen_test_simple import PenTestSimpleTool
        from src.tools.policy_check import PolicyCheckTool
        from src.tools.sast_scan import SastScanTool
        from src.tools.secret_scan import SecretScanTool
        from src.tools.security_scan import SecurityScanTool
        from src.tools.slo_check import SloCheckTool

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
        # AET-15 / AET-16 — focused replacements for the monolithic
        # security_scan. sast_scan handles SAST (bandit + eslint),
        # dependency_audit handles supply-chain CVEs (pip-audit + npm
        # audit). Both emit unified-severity findings the security
        # gate (AET-20) can consume directly.
        self.tool_registry.register_implementation("sast_scan", SastScanTool())
        self.tool_registry.register_implementation("dependency_audit", DependencyAuditTool())
        # AET-17 — pre-`code_commit` secret detector. Runs on the
        # agent's IN-MEMORY emissions, not on disk, so a leaked key is
        # caught before code_writer / github_publisher materialises it.
        self.tool_registry.register_implementation("secret_scan", SecretScanTool())
        # AET-18 — black-box probe against the project's finalized
        # OpenAPI spec. Read-only where possible; mutating probes only
        # on endpoints the spec declares as POST/PUT. Skips when no
        # spec or base_url is available.
        self.tool_registry.register_implementation("pen_test_simple", PenTestSimpleTool())
        # AET-25 — rolling-window SLO evaluator for the ops_heal_agent.
        # Reads deploy_health rows through the StateStore, so the
        # constructor takes the same `state` handle wait_for_deployment
        # uses. No state → tool returns ERROR at call time rather than
        # crashing at boot.
        self.tool_registry.register_implementation(
            "slo_check", SloCheckTool(state=self.state),
        )
        # AET-26 — single-shot HTTP probe (same primitive the supervisor
        # uses, exposed as a tool so the agent can run on-demand probes
        # between supervisor ticks).
        self.tool_registry.register_implementation(
            "health_probe", HealthProbeTool(),
        )
        # AET-27 — z-score anomaly detector over rolling 1hr / 5min
        # windows. Complementary to slo_check (absolute thresholds vs
        # relative deviation).
        self.tool_registry.register_implementation(
            "anomaly_detect", AnomalyDetectTool(state=self.state),
        )
        # AET-29 — idempotent rollback request queue. Writes a
        # rollback_requests row; supervisor host process consumes it
        # and runs the actual git revert + redeploy.
        self.tool_registry.register_implementation(
            "auto_rollback", AutoRollbackTool(state=self.state),
        )
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

    async def _resolve_model_for_agent(
        self,
        agent_id: str,
        request_provider: str | None = None,
    ) -> tuple[Any, str | None, str | None, str | None]:
        """Return ``(client, model_id, tool_calling_mode, source)`` for
        the given agent.

        PAM-07: Replaces the old ``_resolve_provider()`` indirection.
        When the catalog + resolver are wired (the common path), the
        5-layer precedence chain in ``ModelResolver.resolve()`` decides
        which model wins. When the resolver is unavailable (catalog
        load failed at boot — see warning in ``__init__``), we fall
        back to the legacy values: the AnthropicAWS client + the
        agent's YAML model. The caller passes these into
        ``BaseAgent.process_task()`` as kwargs so concurrent calls on
        the same agent can carry different models without racing on
        instance state (PAM-06's contract).
        """
        if self.model_resolver is not None:
            try:
                resolved = await self.model_resolver.resolve(
                    agent_id=agent_id,
                    request_provider=request_provider,
                )
                return (
                    resolved.client,
                    resolved.vendor_model_id,
                    resolved.tool_calling_mode,
                    resolved.resolution_source,
                )
            except Exception as e:  # noqa: BLE001
                # Resolver hiccup: log + fall through to legacy path.
                # MUST NOT block dispatch on a resolution failure — same
                # principle the resolver itself applies to its DB layer.
                logger.warning(
                    "model_resolver_failed_falling_back",
                    agent_id=agent_id, error=str(e),
                )
        # Legacy path: single AnthropicAWS client + per-agent YAML model.
        agent = self.registry.get(agent_id)
        agent_model = agent.model if agent else None
        return (self.anthropic_client, agent_model, "native", "legacy_fallback")

    async def _resolve_kb_for_request(
        self, agent_id: str, request_id: str, inputs: dict[str, Any],
    ) -> tuple[Any, Any, dict[str, Any] | None]:
        """Return ``(kb_scope, kb_retriever, retrieval_config)`` for this
        invocation, or ``(None, None, None)`` when the KB is unavailable.

        KB-09: builds the grounding scope from the Request's ``bucket_ids``
        (the user's per-request bucket selection) + the agent's YAML
        ``retrieval:`` config.

        KB-15: the **namespace** is derived from the Request's ``project_id``
        (the per-application isolation boundary) honouring the agent's YAML
        ``retrieval.scope`` grant — a project Request scopes the agent to
        ``kb_project_<id>`` so it can't reach another app's knowledge. The
        agent's tool schema has no namespace param, so it cannot widen.

        Gated on subsystem availability — when the KB is down, returns all-None
        and the agent runs exactly as pre-KB.
        """
        from src.knowledge.scoping import resolve_craft_namespace, resolve_namespace
        from src.knowledge.tools import KbScope

        sub = self.kb_subsystem
        if sub is None or not getattr(sub, "available", False):
            return None, None, None
        retrieval_config = (self.config.agents.get(agent_id, {}) or {}).get("retrieval")

        # Fetch the Request once for BOTH its project_id (→ namespace) and its
        # selected bucket_ids (→ in-namespace refinement). inputs may override
        # bucket_ids (workflow-passed) and project_id (single_agent_call path).
        project_id: str | None = None
        bucket_ids: list[str] = []
        if isinstance(inputs, dict) and inputs.get("bucket_ids"):
            bucket_ids = list(inputs["bucket_ids"])
        if isinstance(inputs, dict) and inputs.get("project_id"):
            project_id = str(inputs["project_id"])
        if project_id is None or not bucket_ids:
            try:
                request = await self.state.get_request(request_id) if self.state else None
                if request is not None:
                    if project_id is None:
                        project_id = getattr(request, "project_id", None)
                    if not bucket_ids:
                        bucket_ids = list(getattr(request, "bucket_ids", []) or [])
            except Exception as e:  # noqa: BLE001
                logger.warning("kb_scope_request_lookup_failed", error=str(e))

        agent_scope = str((retrieval_config or {}).get("scope", "auto"))
        namespace = resolve_namespace(sub.settings, project_id, agent_scope)
        craft_namespace = resolve_craft_namespace(sub.settings, project_id, agent_scope)
        # KB-25 — episodic recall is per-app; only scope it when this Request
        # belongs to a real application (not the platform-only / unassigned case).
        from src.models.base import UNASSIGNED_PROJECT_ID

        memory_namespace = (
            sub.settings.memory_namespace(project_id)
            if project_id and project_id != UNASSIGNED_PROJECT_ID
            else None
        )
        # KB-32 — per-Request retrieval budget from the agent's YAML config.
        try:
            max_searches = int((retrieval_config or {}).get("max_searches") or 0) or None
        except (TypeError, ValueError):
            max_searches = None
        scope = KbScope(
            namespace=namespace, craft_namespace=craft_namespace,
            bucket_ids=bucket_ids, agent_id=agent_id, request_id=request_id,
            is_project=namespace != sub.settings.platform_namespace,
            project_id=project_id, memory_namespace=memory_namespace,
            max_searches=max_searches,
        )
        logger.debug(
            "kb_scope_resolved", agent_id=agent_id, namespace=namespace,
            craft_namespace=craft_namespace, project_id=project_id,
            buckets=len(bucket_ids), scope=agent_scope,
        )
        return scope, sub.retriever, retrieval_config

    async def execute(
        self, agent_id: str, request_id: str, inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an agent task. Falls back to mock if the client isn't configured.

        PAM-07: The model is resolved per-invocation through
        ``ModelResolver`` and threaded into ``process_task()`` as
        kwargs. No more snapshot-and-restore of ``self._llm_client`` /
        ``self.model`` on the agent (which was the race we worked
        around before PAM-06). A per-request provider override can be
        passed in via ``inputs["provider"]`` (the Command Center submit
        form sets this; in-flight retries persist it on Request.provider).
        """
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

        # PAM-07: per-call model resolution. ``request_provider`` from
        # the inputs dict feeds layer 1 of the resolver chain (request
        # override beats DB override beats YAML default beats env beats
        # catalog default).
        request_provider = None
        if isinstance(inputs, dict):
            request_provider = (
                inputs.get("provider")
                or inputs.get("request_provider")
                or None
            )
        (
            llm_client,
            resolved_model,
            tool_calling_mode,
            source,
        ) = await self._resolve_model_for_agent(
            agent_id=agent_id, request_provider=request_provider,
        )

        # KB-09: resolve the knowledge grounding scope + retriever for this
        # invocation (all-None when the KB is unavailable → no behaviour change).
        kb_scope, kb_retriever, retrieval_config = await self._resolve_kb_for_request(
            agent_id, request_id, inputs,
        )

        if not llm_client:
            logger.info(
                "agent_executing_mock",
                agent_id=agent_id, request_id=request_id,
                project_root=str(project_root) if project_root else "platform",
                resolution_source=source,
            )
            return await agent.process_task(
                request_id, inputs, project_root=project_root,
                llm_client=None,
                model=resolved_model,
                tool_calling_mode=tool_calling_mode,
                kb_scope=kb_scope,
                kb_retriever=kb_retriever,
                retrieval_config=retrieval_config,
            )

        logger.info(
            "agent_executing",
            agent_id=agent_id, request_id=request_id,
            model=resolved_model,
            tool_calling_mode=tool_calling_mode,
            resolution_source=source,
            inference_geo=self.inference_geo or "global",
            project_root=str(project_root) if project_root else "platform",
            kb_buckets=len(kb_scope.bucket_ids) if kb_scope else 0,
            kb_grounded=kb_retriever is not None,
        )
        result = await agent.process_task(
            request_id, inputs, project_root=project_root,
            llm_client=llm_client,
            model=resolved_model,
            tool_calling_mode=tool_calling_mode,
            kb_scope=kb_scope,
            kb_retriever=kb_retriever,
            retrieval_config=retrieval_config,
        )
        # KB-20 — record this agent's decision into the provenance ledger.
        # Auto-derived from the trace unless the agent called record_decision
        # itself. Soft-fails; never affects the agent result.
        await self._auto_record_decision(
            agent_id, request_id, kb_scope, retrieval_config, inputs, result,
        )
        return result

    async def _auto_record_decision(
        self, agent_id: str, request_id: str, kb_scope: Any,
        retrieval_config: dict[str, Any] | None, inputs: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """KB-20 — if a KB-grounded agent finished without explicitly calling
        ``record_decision``, derive one from the trace: conclusion (output
        text) + the chunks its retrievals returned + an inputs digest. Only for
        agents actually wired to the KB (``retrieval`` config present)."""
        sub = self.kb_subsystem
        if sub is None or not getattr(sub, "available", False):
            return
        if kb_scope is None or not retrieval_config:
            return
        try:
            store = sub.knowledge_store
            # Skip if the agent already recorded its own decision this request.
            existing = await store.list_decisions(request_id)
            if any(d.get("agent_id") == agent_id for d in existing):
                return
            summary = str((result or {}).get("text", "")).strip()
            if not summary:
                return
            # Chunks this agent's retrievals surfaced (provenance).
            audit = await store.list_retrieval_audit(request_id)
            chunk_ids: list[str] = []
            for a in audit:
                if a.get("agent_id") == agent_id:
                    chunk_ids.extend(a.get("returned_chunk_ids") or [])
            chunk_ids = list(dict.fromkeys(chunk_ids))[:50]
            digest = hashlib.sha256(
                repr(sorted(inputs.items()) if isinstance(inputs, dict) else inputs).encode()
            ).hexdigest()[:16]
            await store.record_decision(
                request_id=request_id, agent_id=agent_id, summary=summary[:2000],
                project_id=getattr(kb_scope, "project_id", None),
                retrieved_chunk_ids=chunk_ids, inputs_digest=digest,
            )
            logger.debug(
                "kb_decision_auto_recorded", agent_id=agent_id,
                request_id=request_id, chunks=len(chunk_ids),
            )
        except Exception as e:  # noqa: BLE001 — provenance must never block work
            logger.warning("kb_auto_record_decision_failed", error=str(e))

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
        # PAM-07: resolve per-call so single_agent_call benefits from the
        # same model-resolution chain as the workflow-driven execute()
        # path. Falls back to (anthropic_client, agent.model) when the
        # resolver isn't wired.
        (
            llm_client,
            resolved_model,
            tool_calling_mode,
            _source,
        ) = await self._resolve_model_for_agent(agent_id=agent_id)
        try:
            result = await agent.single_call(
                prompt, max_tokens=max_tokens,
                llm_client=llm_client,
                model=resolved_model,
                tool_calling_mode=tool_calling_mode,
            )
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
