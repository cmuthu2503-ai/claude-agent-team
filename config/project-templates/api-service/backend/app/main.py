"""{{PROJECT_NAME}} — API service entry point.

Scaffolded by the Agent Team platform. Edit freely.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

PROJECT_NAME = os.getenv("PROJECT_NAME", "{{PROJECT_NAME}}")

app = FastAPI(title=PROJECT_NAME, version="0.1.0")

# Permissive CORS for local dev. Tighten once a frontend or known
# caller is in play.
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
        "note": "API scaffold is live. Edit backend/app/main.py to extend.",
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict:
    """Healthcheck — used by docker-compose AND by the platform's
    Deploy endpoint."""
    return {"status": "healthy", "project": PROJECT_NAME}
