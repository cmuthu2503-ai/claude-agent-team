"""{{PROJECT_NAME}} — backend entry point.

Scaffolded by the Agent Team platform. Edit freely; agent emissions
will replace whole files when tasks dispatch.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

PROJECT_NAME = os.getenv("PROJECT_NAME", "{{PROJECT_NAME}}")

app = FastAPI(title=PROJECT_NAME, version="0.1.0")

# Permissive CORS for local dev — the frontend's Vite proxy talks to
# the in-network `backend:8000`, but if you hit the API directly from
# the host's browser at :{{BACKEND_PORT}} you'll need this. Tighten
# `allow_origins` once the deployed URL is known.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict:
    """Sanity endpoint. Replace with your real API surface."""
    return {
        "message": f"Hello from {PROJECT_NAME}",
        "note": "Backend scaffold is live. Edit backend/app/main.py to extend.",
    }


@app.get("/health")
def health() -> dict:
    """Healthcheck — used by the per-project docker-compose healthcheck
    AND by the platform's Deploy endpoint when verifying the stack came
    up cleanly."""
    return {"status": "healthy", "project": PROJECT_NAME}
