"""Agent Team Backend — FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import agents, auth, cost, notifications, releases, requests, users
from src.api.websocket import router as ws_router
from src.auth.service import AuthService
from src.config.loader import ConfigLoader
from src.core.events import EventEmitter
from src.core.orchestrator import Orchestrator
from src.state.sqlite_store import SQLiteStateStore

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
    jwt_secret = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
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

# CORS
origins_map = {
    "development": ["http://localhost:3000"],
    "staging": ["http://localhost:3010"],
    "production": ["http://localhost:3020"],
    "demo": ["http://localhost:3030"],
}
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins_map.get(ENVIRONMENT, ["http://localhost:3000"]),
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
app.include_router(cost.router)
app.include_router(ws_router)


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": ENVIRONMENT,
    }
