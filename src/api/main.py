"""CrewAI API — FastAPI application factory mounted at /api/v1.

NOTE: This is a SEPARATE ASGI application from the legacy `src/main.py`
(which serves "Agent Team API" v0.1.0). The two apps run independently;
this one is launched via `uvicorn src.api.main:app`. The PRD §3b call to
"update src/main.py" was intentionally NOT performed because doing so
would orphan the 11 routers wired into the legacy entrypoint.

Wires the four cross-cutting concerns required by API Spec §2/§4.7/§8:
  1. CORS (configurable via CREWAI_CORS_ORIGINS env)
  2. Response envelope {data, meta, error}
  3. Request-ID propagation (X-Request-ID UUIDv4)
  4. RFC 7807 problem+json error handlers

Middleware ordering: Starlette runs middleware LIFO on the REQUEST path
and FIFO on the RESPONSE path. We register in this order:

    add_middleware(CORSMiddleware)        # outermost
    add_middleware(EnvelopeMiddleware)
    add_middleware(RequestIDMiddleware)   # innermost — runs first on request

So on request: RequestID → Envelope → CORS → route, and on response:
route → CORS → Envelope → RequestID. This guarantees
`request.state.request_id` is populated BEFORE the envelope middleware
reads it on the way back out.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.errors import register_exception_handlers
from src.api.middleware.envelope import EnvelopeMiddleware
from src.api.middleware.request_id import RequestIDMiddleware
from src.api.routes import health as health_routes

DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://localhost:3010",
    "http://localhost:3020",
]


def _parse_cors_origins() -> list[str]:
    """Parse CREWAI_CORS_ORIGINS env var into a list.

    Empty / whitespace-only / unset → default localhost trio.
    Never returns ['*']: that would break allow_credentials=True.
    """
    raw = os.getenv("CREWAI_CORS_ORIGINS", "")
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    return parts or list(DEFAULT_CORS_ORIGINS)


def create_app() -> FastAPI:
    """Build and return the CrewAI FastAPI application."""
    app = FastAPI(
        title="CrewAI API",
        version="1.0.0",
        docs_url="/api/v1/docs",
        redoc_url="/api/v1/redoc",
        openapi_url="/api/v1/openapi.json",
    )

    # --- Middleware (registered outer → inner; runs inner → outer on request) ---
    cors_origins = _parse_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(EnvelopeMiddleware)
    app.add_middleware(RequestIDMiddleware)

    # --- Exception handlers (RFC 7807 problem+json) ---
    register_exception_handlers(app)

    # --- Routes mounted under /api/v1 ---
    v1_router = APIRouter(prefix="/api/v1")
    v1_router.include_router(health_routes.router)
    app.include_router(v1_router)

    return app


app = create_app()
