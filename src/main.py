"""Agent Team Backend — FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import agents, auth, cost, documents, notifications, prompts, releases, requests, users
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

    # Initialize state store
    db_path = os.getenv("DATABASE_PATH", "data/agent_team.db")
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
    agent_executor = AgentSystemExecutor(config)
    if agent_executor.client:
        orchestrator.set_agent_executor(agent_executor)
        logger.info("agent_system_connected", mode="real_llm")
    else:
        logger.info("agent_system_connected", mode="mock")

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
    "staging":     ["http://localhost:3010"],
    "production":  ["http://localhost:3020"],
    "demo":        ["http://localhost:3030"],
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

# Register routers
app.include_router(auth.router)
app.include_router(requests.router)
app.include_router(agents.router)
app.include_router(releases.router)
app.include_router(notifications.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(cost.router)
app.include_router(prompts.router)
app.include_router(ws_router)


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": ENVIRONMENT,
    }
