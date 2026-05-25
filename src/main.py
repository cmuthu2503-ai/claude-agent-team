"""Agent Team Backend — FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.middleware.sse_headers import SSEHeadersMiddleware
from src.api.routes import (
    agents,
    auth,
    cost,
    documents,
    notifications,
    ops,
    projects,
    prompts,
    releases,
    requests,
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

    # `state` threaded in so tools like `wait_for_deployment` (used by
    # devops_specialist) can read deployment_states without opening a
    # parallel SQLite connection.
    agent_executor = AgentSystemExecutor(config, state=state)
    # Stash on app.state so routes (e.g. /projects/:id/prd/generate via
    # PDB-05's single_agent_call) can reach the executor directly without
    # going through the workflow runner.
    app.state.agent_executor = agent_executor
    if agent_executor.client:
        orchestrator.set_agent_executor(agent_executor)
        logger.info("agent_system_connected", mode="real_llm")
    else:
        logger.info("agent_system_connected", mode="mock")

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

    logger.info("backend_started", environment=ENVIRONMENT)
    yield

    # Shutdown
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

# Register routers
app.include_router(auth.router)
app.include_router(requests.router)
app.include_router(agents.router)
app.include_router(releases.router)
app.include_router(notifications.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(projects.router)
app.include_router(cost.router)
app.include_router(prompts.router)
app.include_router(ops.router)
app.include_router(ws_router)


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": ENVIRONMENT,
    }


# Top-level /health for the docker-compose smoke test
# (`curl http://localhost:8000/health`). Kept distinct from the versioned
# /api/v1/health so future API-version bumps can't accidentally break the
# container-orchestration probe.
@app.get("/health")
async def health_root() -> dict:
    return {"status": "ok"}
