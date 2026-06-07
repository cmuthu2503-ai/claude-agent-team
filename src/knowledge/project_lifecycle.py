"""KB-13 — per-project KB provisioning + teardown (Phase 2).

An EventEmitter handler that keeps each Project's isolated knowledge namespace
(``kb_project_<id>``) in lockstep with the project's lifecycle:

  - ``project.created`` → provision the project's KB (its default grounding
    bucket). The namespace is a string scope (no DDL), so this is cheap.
  - ``project.deleted`` → purge ALL of that project's KB — documents, chunks,
    membership, buckets, retrieval audit, decision ledger. Right-to-be-
    forgotten at the project grain (NFR-005 / the per-app isolation guarantee).

Soft-fail by design: a KB hiccup must never block project create/delete or
event broadcasting. When the KB subsystem is unavailable the handler no-ops.
Registered from main.py's lifespan only when the subsystem is available, same
as the other EventEmitter handlers.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# Where local project source trees are mounted in the container
# (docker-compose: C:/ai-projects → /host/ai-projects). For the
# enhance-existing flow (KB-19) a project named <Name> has its docs at
# /host/ai-projects/<Name>/.
_PROJECT_SOURCE_ROOT = Path("/host/ai-projects")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def make_kb_project_handler(
    subsystem: Any,
) -> Callable[[str, dict[str, Any]], Awaitable[None]]:
    """Return an EventEmitter-compatible ``async handler(event_type, data)``
    bound to a built knowledge subsystem."""

    async def handler(event_type: str, data: dict[str, Any]) -> None:
        if event_type not in ("project.created", "project.deleted"):
            return
        if subsystem is None or not getattr(subsystem, "available", False):
            return
        project_id = data.get("project_id")
        if not project_id:
            return
        store = subsystem.knowledge_store
        namespace = subsystem.settings.project_namespace(project_id)
        try:
            if event_type == "project.created":
                await store.provision_project(project_id, namespace)
                # KB-19 — enhance-existing seeding. If the project already has a
                # local source tree on disk, seed its docs into the project KB
                # so agents start grounded. No-op for brand-new projects (the
                # cold-start sparse path then kicks in).
                await _maybe_seed(subsystem, project_id, namespace, data.get("name"))
            else:  # project.deleted
                # KB-24 — also purge the project's episodic memory namespace
                # (mem_project_<id>). Right-to-be-forgotten across both stores.
                mem_ns = subsystem.settings.memory_namespace(project_id)
                await store.purge_project(project_id, namespace, memory_namespace=mem_ns)
        except Exception as e:  # noqa: BLE001 — never block lifecycle on the KB
            # NB: don't pass ``event=`` — structlog reserves it for the message.
            logger.warning(
                "kb_project_lifecycle_failed",
                event_name=event_type, project_id=project_id, error=str(e),
            )

    return handler


async def _maybe_seed(
    subsystem: Any, project_id: str, namespace: str, name: str | None
) -> None:
    """Seed the project KB from its local source tree if one exists. Soft-fail
    + path-safe (the project name is filesystem-validated upstream, but we
    re-check to refuse traversal)."""
    if not name or not _SAFE_NAME.match(name):
        return
    root = _PROJECT_SOURCE_ROOT / name
    if not root.is_dir():
        return
    from src.knowledge.project_seed import seed_project_corpus

    summary = await seed_project_corpus(
        pipeline=subsystem.pipeline, store=subsystem.knowledge_store,
        project_id=project_id, namespace=namespace, root=root,
    )
    if summary.ingested or summary.skipped:
        logger.info(
            "kb_project_enhance_seeded", project_id=project_id,
            ingested=summary.ingested, skipped=summary.skipped,
        )
