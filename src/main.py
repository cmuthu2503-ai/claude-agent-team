"""Agent Team Backend — FastAPI application entry point."""

import os
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.api.middleware.body_size_limit import BodySizeLimitMiddleware
from src.api.middleware.service_token_write_block import ServiceTokenWriteBlockMiddleware
from src.api.middleware.sse_headers import SSEHeadersMiddleware
from src.api.routes import (
    agents,
    auth,
    cost,
    documents,
    knowledge,
    lessons,
    models,
    notifications,
    ops,
    projects,
    prompts,
    proposals,
    releases,
    requests,
    service_tokens,
    users,
)
from src.api.websocket import router as ws_router
from src.auth.service import AuthService
from src.config.loader import ConfigLoader
from src.core.events import EventEmitter
from src.core.orchestrator import Orchestrator
from src.state.sqlite_store import SQLiteStateStore
from src.utils.secrets import read_secret

logger = structlog.get_logger()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Environments where running fake (mock) agents is NEVER acceptable — a missing
# credential there is a deploy incident, not a reason to silently serve
# simulated output.
_MOCK_FORBIDDEN_ENVIRONMENTS = frozenset({"staging", "production"})


class MockModeNotAllowedError(RuntimeError):
    """Raised at startup when the agent system would fall back to MOCK mode
    (no LLM credentials) but mock mode is not explicitly allowed. Prevents the
    silent-fake-output footgun: a misconfigured prod/staging deploy fails loudly
    instead of serving realistic-looking simulated results."""


def _env_flag(name: str) -> bool:
    """Truthy parse for an env flag: 1/true/yes/on (case-insensitive)."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_agent_mode(*, has_client: bool, environment: str, allow_mock: bool) -> str:
    """Decide the agent execution mode at boot, or refuse to boot.

    - Real LLM client present       → "real_llm" (always).
    - No client, forbidden env       → raise (staging/production never run fake agents).
    - No client, mock not opted in   → raise (default-deny the silent fallback).
    - No client, mock opted in       → "mock" (caller MUST log a loud warning).

    Pure function so the policy is unit-testable without booting the app.
    """
    if has_client:
        return "real_llm"
    if environment in _MOCK_FORBIDDEN_ENVIRONMENTS:
        raise MockModeNotAllowedError(
            f"No LLM credentials configured and ENVIRONMENT={environment!r} forbids "
            "mock mode. Refusing to boot with fake agents — configure Claude Platform "
            "on AWS credentials (see docs/setup-claude-platform-on-aws.md)."
        )
    if not allow_mock:
        raise MockModeNotAllowedError(
            "No LLM credentials configured. The agent system would run in MOCK mode "
            "(SIMULATED, fake agent output). This is disabled by default to prevent "
            "silently serving fake results. For local dev / demo / tests, set "
            "ALLOW_MOCK_MODE=true explicitly. For real execution, configure credentials "
            "(see docs/setup-claude-platform-on-aws.md)."
        )
    return "mock"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup, cleanup on shutdown."""
    # Load config
    config = ConfigLoader()
    config.load_all()
    app.state.config = config

    # Initialize state store. Resolution order:
    #   1. CREWAI_DB_PATH — preferred; set by docker-compose to
    #      /app/data/crewai.db on the `crewai_data` named volume.
    #   2. AIAGENT_DB_PATH — legacy alias kept for older compose files.
    #   3. DATABASE_PATH   — even older alias from the pre-Docker era.
    #   4. ./data/agent_team.db relative to cwd (non-Docker local default).
    db_path = (
        os.getenv("CREWAI_DB_PATH")
        or os.getenv("AIAGENT_DB_PATH")
        or os.getenv("DATABASE_PATH", "data/agent_team.db")
    )
    state = SQLiteStateStore(db_path=db_path)
    await state.initialize()
    app.state.state_store = state

    # Initialize event emitter
    events = EventEmitter()
    app.state.events = events

    # Initialize auth service
    jwt_secret = read_secret("jwt_secret", "JWT_SECRET", "dev-secret-change-in-production")
    auth_config = config.project.get("auth", {})
    auth_service = AuthService(
        state=state,
        secret_key=jwt_secret,
        access_token_minutes=auth_config.get("access_token_lifetime_minutes", 30),
        refresh_token_days=auth_config.get("refresh_token_lifetime_days", 7),
    )
    app.state.auth_service = auth_service

    # Bootstrap admin user on first run
    admin_password = await auth_service.bootstrap_admin()
    if admin_password:
        logger.info("first_run_admin_created", username="admin", password=admin_password)

    # Initialize orchestrator
    orchestrator = Orchestrator(config=config, state=state, events=events)
    app.state.orchestrator = orchestrator

    # Initialize agent system with real LLM (if API key is set)
    from src.agents.executor import AgentSystemExecutor

    # PAM-14 — ORDERING INVARIANT (do not reshuffle):
    #
    # ``state`` MUST be initialized (line above) before
    # ``AgentSystemExecutor`` is constructed. The executor's
    # ``__init__`` builds the ``ModelResolver`` and hands it
    # ``state_store=self.state`` so layer 2 of the 5-layer model-
    # resolution chain (DB override) is live from the first dispatch.
    # If ``state`` were ``None`` here, the resolver would silently
    # skip layer 2 forever — operator-set overrides in
    # ``agent_model_overrides`` would be ignored at runtime, and the
    # Team Status "override active" badges would lie.
    #
    # ``state`` is also threaded in for tools like
    # ``wait_for_deployment`` (used by devops_specialist) so they can
    # read ``deployment_states`` without opening a parallel SQLite
    # connection.
    agent_executor = AgentSystemExecutor(config, state=state)
    # Stash on app.state so routes (e.g. /projects/:id/prd/generate via
    # PDB-05's single_agent_call) can reach the executor directly without
    # going through the workflow runner.
    app.state.agent_executor = agent_executor

    # HAI-24 — the approval gate's action registry. Gated action handlers are
    # registered into this by the P3 lifecycle tasks; the confirm endpoint
    # (HAI-26) executes a confirmed proposal through it.
    from src.core.proposal_registry import ProposalActionRegistry

    app.state.proposal_registry = ProposalActionRegistry()

    # P3 (HAI-33..42) — register the gated-action handlers a confirmed proposal
    # executes. Actions without a handler stay gated-but-unhandled (fail cleanly).
    from src.core.proposal_handlers import register_all as _register_proposal_handlers

    _register_proposal_handlers(app.state.proposal_registry)

    # HAI-63 — governed mode. When ON (the default — "nothing moves without my
    # approval"), in-process callers that mutate gated state route through the
    # approval gate (src.core.in_process_gate) and become proposals instead of
    # executing. Set GOVERNED_MODE=false to restore legacy autonomous behavior.
    app.state.governed_mode = os.getenv("GOVERNED_MODE", "true").strip().lower() not in (
        "false", "0", "no", "off",
    )
    logger.info("governance_mode", governed=app.state.governed_mode)

    # Decide real-vs-mock at boot, or refuse to boot. Mock mode (fake agent
    # output) is now OPT-IN: missing credentials hard-fail by default, and are
    # forbidden outright in staging/production, so a misconfigured deploy can
    # never silently serve simulated results.
    agent_mode = resolve_agent_mode(
        has_client=bool(agent_executor.client),
        environment=ENVIRONMENT,
        allow_mock=_env_flag("ALLOW_MOCK_MODE"),
    )
    app.state.agent_mode = agent_mode
    if agent_mode == "real_llm":
        orchestrator.set_agent_executor(agent_executor)
        logger.info("agent_system_connected", mode="real_llm")
    else:
        logger.warning(
            "agent_system_MOCK_MODE_ENABLED",
            mode="mock",
            environment=ENVIRONMENT,
            warning=(
                "Agents are SIMULATED — ALL outputs are fake. Enabled via "
                "ALLOW_MOCK_MODE. Do NOT use for real work."
            ),
        )

    # KB-10 — Knowledge Base subsystem. build_knowledge_subsystem SOFT-FAILS
    # (NFR-007): model fails to load or unreachable Postgres → available=False and
    # the platform boots exactly as before (no retrieval, no ingest). When it
    # IS available we (a) stash it on app.state for the /knowledge routes, (b)
    # hand it to the executor so per-request grounding resolves (KB-09 reads
    # executor.kb_subsystem), and (c) register the knowledge_search/get tools
    # into the executor's registry so granted agents can call them.
    from src.knowledge.subsystem import build_knowledge_subsystem
    from src.knowledge.tools import register_knowledge_tools

    kb_subsystem = await build_knowledge_subsystem(config)
    app.state.kb_subsystem = kb_subsystem
    agent_executor.kb_subsystem = kb_subsystem
    if kb_subsystem.available:
        register_knowledge_tools(agent_executor.tool_registry, kb_subsystem)
        # KB-13 (Phase 2) — keep each project's isolated kb_project_<id>
        # namespace in lockstep with its lifecycle: provision on
        # project.created, purge on project.deleted. Soft-fails internally.
        from src.knowledge.memory_capture import make_memory_capture_handler
        from src.knowledge.project_ingest import make_kb_artifact_ingest_handler
        from src.knowledge.project_lifecycle import make_kb_project_handler

        events.on(make_kb_project_handler(kb_subsystem))
        # KB-14 (Phase 2) — auto-ingest a project's APPROVED artifacts
        # (finalized PRD/spec/tasks, successful commit, published research)
        # into its kb_project_<id> namespace. Soft-fails internally.
        events.on(make_kb_artifact_ingest_handler(kb_subsystem, state))
        # KB-24 (Phase 4) — auto-capture one episodic-memory row per
        # completed/failed Request into mem_project_<id> (unvetted, decaying,
        # never citeable as fact). Soft-fails internally.
        events.on(make_memory_capture_handler(kb_subsystem, state))
        # KB-26 (Phase 4) — periodic consolidation job: fold old raw episodes
        # into compact summaries + propose recurring patterns for promotion
        # (never auto-promote). Background asyncio task, cancelled on shutdown.
        import asyncio as _asyncio

        from src.knowledge.consolidation import make_consolidation_job
        from src.knowledge.retention import make_retention_job

        app.state.kb_consolidation_task = _asyncio.create_task(
            make_consolidation_job(kb_subsystem)(), name="kb_consolidation",
        )
        # KB-30 (Phase 5) — retention sweep: TTL expiry + relevance pruning of
        # unused episodes. Right-to-be-forgotten purges run on-demand via the
        # /knowledge/forget route. Background asyncio task, cancelled on shutdown.
        app.state.kb_retention_task = _asyncio.create_task(
            make_retention_job(kb_subsystem)(), name="kb_retention",
        )
        logger.info(
            "knowledge_base_connected",
            namespace=kb_subsystem.settings.platform_namespace,
            project_provisioning="on",
            artifact_auto_ingest="on",
            episodic_capture="on",
            consolidation="on",
            retention="on",
        )
    else:
        logger.info("knowledge_base_unavailable", reason=kb_subsystem.reason)

    # PDB-25 — server-side handler that maps `request.*` events to
    # `project_tasks.task_status` updates whenever a Request was created
    # from a project task (i.e. source_task_id is set).
    from src.core.project_task_status import make_project_task_status_handler

    events.on(make_project_task_status_handler(state))
    logger.info("project_task_status_handler_registered")

    # BPD-24 — auto-dispatch handler. Listens for request.completed /
    # request.status_changed and, when the project has
    # auto_dispatch_on_deploy=True, fires every newly-unblocked task
    # via the orchestrator. Emits project.tasks.auto_dispatched
    # (BPD-26) so the UI can render the cascade live.
    from src.core.auto_dispatch import make_auto_dispatch_handler

    events.on(make_auto_dispatch_handler(state, orchestrator, events))
    logger.info("auto_dispatch_handler_registered")

    # AET-11 — self-learning handler. Listens for request.failed events
    # and fires the self_learning_agent (silent single_agent_call) with
    # a structured failure context: {request_id, final_error, retries,
    # agent_traces[:1000], related_files}. The agent reads existing
    # lessons, jaccard-dedups against them via lessons_writer (AET-09),
    # and appends a new lesson if the pattern is novel.
    #
    # Skipped when there's no LLM client (mock mode) — the agent would
    # just produce a placeholder string. Real benefits only when
    # connected to Claude Platform on AWS.
    if agent_executor.client:
        from src.core.self_learning_trigger import make_self_learning_handler

        events.on(make_self_learning_handler(state, agent_executor, events))
        logger.info("self_learning_handler_registered")
    else:
        logger.info(
            "self_learning_handler_skipped",
            reason="agent_executor running in mock mode (no LLM client)",
        )

    # AET-31 — ops_heal handler + periodic anomaly sweeper. Closes the
    # AE-1 loop: sweeper runs anomaly_detect every 90s against each
    # env's deploy_health rows and emits deploy_health.anomaly_detected
    # when ANOMALY verdict; handler reads that, runs slo_check, decides
    # between ops.alert.fired / ops.rollback.triggered. The decision
    # is DETERMINISTIC (no LLM) so it completes in <100ms — a real
    # outage shouldn't have to wait for model latency to start rolling
    # back. Runs in mock mode too (no LLM required for the sweeper).
    import asyncio

    from src.core.ops_heal_handler import (
        make_anomaly_sweeper,
        make_ops_heal_handler,
    )

    events.on(make_ops_heal_handler(state, agent_executor, events))
    logger.info("ops_heal_handler_registered")
    app.state.ae1_anomaly_sweeper_task = asyncio.create_task(
        make_anomaly_sweeper(state, events, agent_executor)(),
        name="ae1_anomaly_sweeper",
    )
    logger.info("anomaly_sweeper_started")

    # HAI-57 — crash-recovery reconciliation. A proposal confirmed but not yet
    # executed when the process died is stranded in `confirmed`; mark it failed
    # so it never silently hangs (and per HAI-58 the operator re-proposes). Runs
    # ONCE at startup, before we begin serving, and is awaited so a stranded
    # proposal is resolved before any new traffic arrives.
    from src.core.proposal_recovery import reconcile_confirmed_proposals_once

    _reconciled = await reconcile_confirmed_proposals_once(state, events)
    if _reconciled:
        logger.warning("proposal_crash_recovery", count=len(_reconciled), proposal_ids=_reconciled)

    # HAI-29 — proposal auto-expire sweeper. Expires PENDING proposals past their
    # TTL (atomic CAS → never executes) and emits proposal.expired, so an
    # unanswered approval queue can't pile up invisibly.
    from src.core.proposal_expiry import make_proposal_expiry_sweeper

    _proposal_expiry_interval = float(os.getenv("PROPOSAL_EXPIRY_INTERVAL_SECONDS", "60"))
    app.state.proposal_expiry_task = asyncio.create_task(
        make_proposal_expiry_sweeper(state, events, interval=_proposal_expiry_interval)(),
        name="proposal_expiry_sweeper",
    )
    logger.info("proposal_expiry_sweeper_registered")

    # HAI-17/18 — outbound push bridge. Forwards curated events (request.failed,
    # request.completed, deploy_health.anomaly_detected) to a Hermes inbound
    # webhook so Hermes is pinged in real time. Registered ONLY when
    # PUSH_WEBHOOK_URL is set; otherwise push is off and Hermes relies on its
    # scheduled pull (FR-073). Soft-fails — a delivery error never blocks events.
    _push_url = os.getenv("PUSH_WEBHOOK_URL", "").strip()
    if _push_url:
        from src.core.push_bridge import PushBridge

        _push_events_env = os.getenv("PUSH_EVENTS", "").strip()
        _push_events = (
            {e.strip() for e in _push_events_env.split(",") if e.strip()}
            if _push_events_env
            else None
        )
        push_bridge = PushBridge(
            _push_url,
            events=_push_events,
            secret=os.getenv("PUSH_WEBHOOK_SECRET") or None,
        )
        events.on(push_bridge.handler)
        push_bridge.start()  # background delivery worker (HAI-54)
        app.state.push_bridge = push_bridge
        logger.info("push_bridge_registered", events=sorted(_push_events) if _push_events else "default")
    else:
        logger.info("push_bridge_disabled", reason="PUSH_WEBHOOK_URL not set; Hermes uses pull")

    logger.info("backend_started", environment=ENVIRONMENT)
    yield

    # Shutdown — cancel the sweeper cleanly so the task doesn't leak.
    sweeper = getattr(app.state, "ae1_anomaly_sweeper_task", None)
    if sweeper is not None and not sweeper.done():
        sweeper.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await sweeper

    # HAI-17/54 — stop the push-bridge worker cleanly.
    push_bridge = getattr(app.state, "push_bridge", None)
    if push_bridge is not None:
        await push_bridge.stop()

    # HAI-29 — cancel the proposal-expiry sweeper cleanly.
    proposal_expiry = getattr(app.state, "proposal_expiry_task", None)
    if proposal_expiry is not None and not proposal_expiry.done():
        proposal_expiry.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await proposal_expiry

    # KB-26 — cancel the consolidation job cleanly.
    consolidation = getattr(app.state, "kb_consolidation_task", None)
    if consolidation is not None and not consolidation.done():
        consolidation.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await consolidation

    # KB-30 — cancel the retention sweep cleanly.
    retention = getattr(app.state, "kb_retention_task", None)
    if retention is not None and not retention.done():
        retention.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await retention

    # KB-10 — close the KB connection pool (no-op if it never came up).
    kb = getattr(app.state, "kb_subsystem", None)
    if kb is not None:
        await kb.aclose()

    await state.close()
    logger.info("backend_stopped")


app = FastAPI(
    title="Agent Team API",
    version="0.1.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

# CORS — explicit env override wins; otherwise fall back to per-environment localhost
# defaults so dev/staging/prod/demo on a single workstation Just Work.
#
# For remote deployment set CORS_ORIGINS in the prod env file/secrets:
#   CORS_ORIGINS=https://agent-team.example.com,https://staging.example.com
# Comma-separated; whitespace ignored. Wildcards (e.g. "*") are accepted by
# Starlette but break credentialed requests; prefer explicit hostnames.
_cors_default_map = {
    "development": ["http://localhost:3000"],
    "staging": ["http://localhost:3010"],
    "production": ["http://localhost:3020"],
    "demo": ["http://localhost:3030"],
}
_cors_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_env:
    cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    cors_origins = _cors_default_map.get(ENVIRONMENT, ["http://localhost:3000"])

logger.info("cors_origins_configured", origins=cors_origins, environment=ENVIRONMENT)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REQ-005 — proxy-safe headers on any path containing `/events`. Registered
# AFTER CORS so the SSE headers reach the outgoing response unmodified;
# Starlette runs middleware in reverse-registration order on the response
# path, so this wraps closest to the route handlers.
app.add_middleware(SSEHeadersMiddleware)

# AET-18 — body-size cap on /api/v1/auth/* (64 KB). Returns 413 before
# the body is buffered, blocking the DOS-001 pen-test probe (1 MB JSON)
# against refresh/logout. Other routes are untouched.
app.add_middleware(BodySizeLimitMiddleware)

# HAI-51 (FR-015a) — service-token write-block. A request authenticated by a
# SERVICE token may only POST /api/v1/proposals; every other state-changing call
# is rejected 403 here, regardless of the route's own guard. Human (JWT) and read
# traffic pass through untouched. The security backstop that lets the Hermes
# service identity mutate state ONLY via the human-approved proposal flow.
app.add_middleware(ServiceTokenWriteBlockMiddleware)

# Register routers
app.include_router(auth.router)
app.include_router(requests.router)
app.include_router(agents.router)
app.include_router(models.router)
app.include_router(releases.router)
app.include_router(notifications.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(projects.router)
app.include_router(cost.router)
app.include_router(prompts.router)
app.include_router(lessons.router)
app.include_router(ops.router)
app.include_router(knowledge.router)
app.include_router(service_tokens.router)
app.include_router(proposals.router)
app.include_router(ws_router)


@app.get("/api/v1/health")
async def health(request: Request) -> dict:
    # Surface the agent execution mode so "mock" can never hide. A client
    # seeing agent_mode="mock" knows every agent output is simulated.
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": ENVIRONMENT,
        "agent_mode": getattr(request.app.state, "agent_mode", "unknown"),
    }


# Top-level /health for the docker-compose smoke test
# (`curl http://localhost:8000/health`). Kept distinct from the versioned
# /api/v1/health so future API-version bumps can't accidentally break the
# container-orchestration probe.
@app.get("/health")
async def health_root() -> dict:
    return {"status": "ok"}
