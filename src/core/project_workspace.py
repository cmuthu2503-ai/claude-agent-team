"""PM-finalize host filesystem writer.

When the user finalizes a project's PRD or task list, the markdown is
written to ``C:/ai-projects/<ProjectName>/docs/`` on the **host**
filesystem (visible to the user's editor / shells / IDE), in addition to
being persisted in SQLite.

Inside the backend container the host directory is bind-mounted at
``/host/ai-projects/`` (see ``docker-compose.yml``). Override the mount
point with ``HOST_PROJECT_ROOT`` if you've remapped the bind for a
different OS or path layout.

Project names are constrained to filesystem-safe characters by
``validate_name`` (no spaces, no path separators, no Windows-reserved
chars) — see ``src/core/project_validation.py``. That's why we can take
the project name verbatim as the folder name here without further
escaping.

Soft-fail policy: a write failure (permission, disk full, mount missing)
is logged and surfaced as a structured dict in the API response, but
must NOT prevent the SQLite finalize from succeeding. The user can
always retry the host write from the project actions menu, or read the
markdown back out of the artifacts table.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.core.project_validation import validate_name
from src.models.base import ProjectTask, TaskStatus

logger = logging.getLogger(__name__)


# Default mount point inside the container. ``docker-compose.yml`` bind-
# mounts ``C:/ai-projects`` (host) → ``/host/ai-projects`` (container).
# Outside Docker (e.g. unit tests on the host directly), set
# ``HOST_PROJECT_ROOT=C:/ai-projects`` in the env to short-circuit the
# in-container path.
_DEFAULT_HOST_ROOT = "/host/ai-projects"


def _host_root() -> Path:
    """Resolve the root directory under which project folders live.
    Picked up from ``HOST_PROJECT_ROOT`` env if set."""
    return Path(os.getenv("HOST_PROJECT_ROOT") or _DEFAULT_HOST_ROOT)


def project_root_dir(project_name: str) -> Path:
    """Return the ``<host>/<ProjectName>/`` path — the project's working
    tree root. Parent of ``docs/`` and peer of ``.git/``.

    Used by the scaffolder at project creation time and by CodeWriter
    when agent code emissions are routed to a per-project tree.

    Re-runs ``validate_name`` defensively to keep callers honest — a
    stale row whose name predates the validator can't poke outside the
    bind-mount."""
    safe_name = validate_name(project_name)
    return _host_root() / safe_name


def project_docs_dir(project_name: str) -> Path:
    """Return the ``<host>/<ProjectName>/docs/`` path. Does NOT create
    the directory — pair with ``mkdir(parents=True, exist_ok=True)``
    at the caller, or use ``write_*`` helpers below which handle it.

    Re-runs ``validate_name`` defensively even though the API layer
    already does so — if a stale project row from before the validation
    landed has a name with a slash in it, we don't want to silently
    write outside the bind-mount.
    """
    return project_root_dir(project_name) / "docs"


@dataclass
class HostWriteResult:
    """Returned to the API layer so it can include a hint in the
    response body and the UI can render a 'wrote to <path>' line."""

    ok: bool
    path: str  # absolute path inside the container; the bound host path is implicit
    bytes_written: int = 0
    error: str | None = None  # populated on failure (soft-fail)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "path": self.path,
            "bytes": self.bytes_written,
            "error": self.error,
        }


def _write_markdown(project_name: str, filename: str, content: str) -> HostWriteResult:
    """Common path for both PRD and tasks writes. Creates the directory
    on demand. Returns a soft-fail result rather than raising — the
    finalize transaction in SQLite must not be rolled back if the host
    write fails (the user can retry without losing the finalized state).
    """
    try:
        docs_dir = project_docs_dir(project_name)
        docs_dir.mkdir(parents=True, exist_ok=True)
        target = docs_dir / filename
        # Force LF endings to match the markdown stored in SQLite; on
        # Windows the user's editor will translate on display anyway.
        # Encode as utf-8 explicitly so we don't depend on the
        # container's default locale (which is usually C.UTF-8 but not
        # guaranteed).
        data = content.encode("utf-8")
        target.write_bytes(data)
        logger.info(
            "host_write ok project=%s file=%s bytes=%d path=%s",
            project_name, filename, len(data), target,
        )
        return HostWriteResult(ok=True, path=str(target), bytes_written=len(data))
    except ValueError as e:
        # Validation failure — bad project name. This is a logic bug if
        # it happens at finalize time (creation should have rejected
        # the name already), so log at WARNING.
        logger.warning("host_write rejected: %s", e)
        return HostWriteResult(ok=False, path="", error=str(e))
    except OSError as e:
        # Filesystem error — mount missing, permission denied, disk
        # full, etc. Log at ERROR so an operator sees it in the logs.
        logger.error(
            "host_write failed project=%s file=%s err=%s",
            project_name, filename, e,
        )
        return HostWriteResult(ok=False, path="", error=f"{type(e).__name__}: {e}")


def write_finalized_prd(project_name: str, content: str) -> HostWriteResult:
    """PM-finalize — drop the finalized PRD markdown at
    ``<host>/<ProjectName>/docs/PRD.md``."""
    return _write_markdown(project_name, "PRD.md", content)


def write_finalized_tasks(project_name: str, content: str) -> HostWriteResult:
    """PM-finalize — drop the rendered tasks markdown at
    ``<host>/<ProjectName>/docs/tasks.md``."""
    return _write_markdown(project_name, "tasks.md", content)


def write_finalized_api_spec(project_name: str, content: str) -> HostWriteResult:
    """PM-finalize — drop the API specification markdown at
    ``<host>/<ProjectName>/docs/api-spec.md``. Same soft-fail
    semantics as the PRD / tasks writers."""
    return _write_markdown(project_name, "api-spec.md", content)


def delete_host_file(project_name: str, filename: str) -> HostWriteResult:
    """Remove ``<host>/<ProjectName>/docs/<filename>``. Mirrors the
    write helpers — soft-fail with a structured result. No-op (still
    ``ok=True``) if the file was already absent. Does NOT remove the
    ``docs/`` directory or the project folder, in case the user has
    other files there (notes, attachments, etc.)."""
    try:
        docs_dir = project_docs_dir(project_name)
        target = docs_dir / filename
        if not target.exists():
            return HostWriteResult(ok=True, path=str(target), bytes_written=0)
        target.unlink()
        logger.info("host_delete ok project=%s file=%s path=%s",
                    project_name, filename, target)
        return HostWriteResult(ok=True, path=str(target), bytes_written=0)
    except ValueError as e:
        logger.warning("host_delete rejected: %s", e)
        return HostWriteResult(ok=False, path="", error=str(e))
    except OSError as e:
        logger.error("host_delete failed project=%s file=%s err=%s",
                     project_name, filename, e)
        return HostWriteResult(ok=False, path="", error=f"{type(e).__name__}: {e}")


# ── Task-list → markdown renderer ──────────────────────────────────────
# ``project_tasks`` is structured (not a markdown blob), so we render it
# to a detailed markdown document mirroring the Atlas-Advisory tasks
# reference at C:/ai-projects/tech-advisory-v1/references/tasks-atlas-advisory.md.
#
# Structure produced:
#
#   # <Project> — Implementation Tasks
#   > Version, generated date, status
#
#   ## Table of Contents
#   ## Progress Summary  (table by phase)
#   ## Implementation Phases
#
#     ### Phase 1: Foundation
#     #### Task 1: Create project skeleton  · `T-abc12345`
#     **Status**: Backlog · **Priority**: high · **Agent**: devops_specialist
#
#     <description body, including "**Rules**" line + "**Sub-tasks**" bullets
#      as emitted by the user_story_author prompt>
#
# Phase grouping picks up the "Phase N: <theme>" prefix in each task's
# title (which the prompt asks the agent to produce). Tasks without
# a matching prefix go under "Uncategorized" so nothing is lost.


_PHASE_PREFIX_RE = (
    # Captures: (phase_label, rest_of_title). Examples that match:
    #   "Phase 1: Foundation — Build X" → phase="Phase 1: Foundation",
    #                                     rest ="Build X"
    #   "Phase 2: Database — Define schema" → similar
    #   "P3: Deploy — Push image" → phase="P3: Deploy", rest="Push image"
    # The em-dash "—" or regular hyphen "-" separates phase from task.
    r"^\s*(?P<phase>(?:Phase\s+)?P?\d+:\s*[^—\-]+?)\s*[—\-]\s*(?P<rest>.+\S)\s*$"
)


def _phase_sort_key(name: str) -> tuple[int, str]:
    """Sort phases by their numeric prefix, with 'Uncategorized' last."""
    import re
    if name == "Uncategorized":
        return (10_000, name)
    m = re.search(r"\d+", name)
    return (int(m.group()) if m else 9_999, name)


def _task_status_label(status: object) -> str:
    """Human-friendly label for the task_status enum."""
    s = str(status)
    return {
        "backlog": "Backlog",
        "dispatched": "Dispatched",
        "in_progress": "In Progress",
        "review": "Review",
        "testing": "Testing",
        "deployed": "Done",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(s, s.title())


def render_tasks_markdown(
    project_name: str,
    tasks: list[ProjectTask],
    *,
    list_version: int | None = None,
    finalized_at_iso: str | None = None,
) -> str:
    """Render a list of ProjectTask rows into a detailed markdown task
    list following the Atlas-Advisory reference format.

    The agent emits each task's title with a "Phase N: <theme> —
    <task>" prefix and each description as a multi-line block with
    `**Rules**:` and `**Sub-tasks:**` sections — we group by phase
    and inline the description verbatim under each task heading."""
    import re

    pattern = re.compile(_PHASE_PREFIX_RE)

    # ── Pass 1: group tasks by phase, preserving order within group ──
    groups: dict[str, list[tuple[ProjectTask, str]]] = {}
    for t in sorted(tasks, key=lambda x: (x.ordinal, x.task_id)):
        m = pattern.match(t.title or "")
        if m and m.group("phase"):
            phase = m.group("phase").strip()
            display = (m.group("rest") or "").strip() or t.title
        else:
            phase = "Uncategorized"
            display = t.title
        groups.setdefault(phase, []).append((t, display))

    phase_order = sorted(groups.keys(), key=_phase_sort_key)

    lines: list[str] = []

    # ── Header ──
    lines.append(f"# {project_name} — Implementation Tasks")
    lines.append("")
    meta_bits: list[str] = []
    if list_version is not None:
        meta_bits.append(f"**Version**: {list_version}")
    if finalized_at_iso:
        meta_bits.append(f"**Finalized**: {finalized_at_iso}")
    meta_bits.append(f"**Total tasks**: {len(tasks)}")
    lines.append("> " + " · ".join(meta_bits))
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Table of Contents ──
    lines.append("## Table of Contents")
    lines.append("")
    for i, phase in enumerate(phase_order, start=1):
        # Slugify the phase for an anchor: lowercase, spaces+colons → dashes.
        slug = re.sub(r"[^a-z0-9]+", "-", phase.lower()).strip("-")
        lines.append(f"{i}. [{phase}](#{slug})")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Progress Summary table ──
    lines.append("## Progress Summary")
    lines.append("")
    lines.append("| Phase | Tasks | Done | Active | Status |")
    lines.append("|-------|-------|------|--------|--------|")
    overall_total = 0
    overall_done = 0
    for phase in phase_order:
        rows = groups[phase]
        total = len(rows)
        done = sum(1 for t, _ in rows if t.task_status == TaskStatus.DEPLOYED)
        active = sum(
            1 for t, _ in rows
            if t.task_status in {
                TaskStatus.DISPATCHED, TaskStatus.IN_PROGRESS,
                TaskStatus.REVIEW, TaskStatus.TESTING,
            }
        )
        if done == total:
            status_label = "✅ Complete"
        elif active > 0:
            status_label = "🟡 In Progress"
        elif done > 0:
            status_label = f"🟡 {done}/{total}"
        else:
            status_label = "⏳ Pending"
        lines.append(f"| {phase} | {total} | {done} | {active} | {status_label} |")
        overall_total += total
        overall_done += done
    pct = (overall_done * 100 // overall_total) if overall_total else 0
    lines.append("")
    lines.append(f"**Overall**: {overall_done}/{overall_total} tasks done ({pct}%)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Implementation Phases ──
    lines.append("## Implementation Phases")
    lines.append("")

    for phase in phase_order:
        lines.append(f"### {phase}")
        lines.append("")
        for task_idx, (t, display) in enumerate(groups[phase], start=1):
            # Per-task header. We include both an in-phase ordinal
            # ("Task 1, 2 …") and the canonical task_id for cross-ref
            # against the DB / chat / dispatch.
            lines.append(f"#### Task {task_idx}: {display}")
            lines.append("")
            # Meta line — status + priority + agent + id.
            meta_parts: list[str] = []
            meta_parts.append(f"**Status**: {_task_status_label(t.task_status)}")
            meta_parts.append(f"**Priority**: {t.priority}")
            if t.estimated_agent:
                meta_parts.append(f"**Agent**: {t.estimated_agent}")
            meta_parts.append(f"**Task ID**: `{t.task_id}`")
            if t.request_id:
                meta_parts.append(f"**Request**: `{t.request_id}`")
            lines.append(" · ".join(meta_parts))
            lines.append("")
            # Description — emitted verbatim. The agent's prompt
            # asks for it to already be a markdown block containing
            # "**Rules**:", a summary paragraph, and "**Sub-tasks:**"
            # bullets ending with a "**Test**:" sub-task. We just
            # pass it through.
            if t.description:
                lines.append(t.description.rstrip())
                lines.append("")
        lines.append("---")
        lines.append("")

    # ── Footer ──
    lines.append("*Generated by Agent Team · keep this file in sync with the project's `project_tasks` table via the platform UI.*")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
