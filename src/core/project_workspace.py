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
# to a human-readable checklist. Format mirrors ``docs/task-list.md``:
#
#   # Tasks — <Project Name>
#   _List version 3 · finalized at 2026-05-19_
#
#   ## Phase 1: Foundation
#   - [ ] T-001 · Build login form (high · feature_request · frontend_developer)
#         Description text wrapped here if multi-line.
#
# Phases are grouped from the task's title prefix when present
# ("Phase 1: ", "P1: ", etc.). Tasks without a matching prefix go under
# an "Uncategorized" section so nothing is lost.


_PHASE_PREFIX_RE = (
    # Captures: (phase_label, rest_of_title). Tolerates "Phase 1:",
    # "P1:", "Phase 1 -", "1.", and a trailing space.
    r"^\s*(?:(?P<phase>(?:Phase\s+)?P?\d+(?:[.:\-]|\s+:))\s*)?(?P<rest>.*\S)\s*$"
)


def render_tasks_markdown(
    project_name: str,
    tasks: list[ProjectTask],
    *,
    list_version: int | None = None,
    finalized_at_iso: str | None = None,
) -> str:
    """Pure function — convert a list of ProjectTask rows to a
    markdown checklist. Sorted by ordinal within each phase group.
    Suitable for round-tripping through ``write_finalized_tasks``.
    """
    import re

    lines: list[str] = []
    lines.append(f"# Tasks — {project_name}")
    if list_version is not None:
        suffix = f"_List version {list_version}"
        if finalized_at_iso:
            suffix += f" · finalized at {finalized_at_iso}"
        suffix += "_"
        lines.append(suffix)
    lines.append("")

    # Group by phase prefix (best-effort). The displayed title strips
    # the prefix so we don't double-print it.
    pattern = re.compile(_PHASE_PREFIX_RE)
    groups: dict[str, list[tuple[ProjectTask, str]]] = {}
    for t in sorted(tasks, key=lambda x: (x.ordinal, x.task_id)):
        m = pattern.match(t.title or "")
        if m and m.group("phase"):
            phase = m.group("phase").rstrip(" :.-").strip()
            display = m.group("rest") or t.title
        else:
            phase = "Uncategorized"
            display = t.title
        groups.setdefault(phase, []).append((t, display))

    # Stable order: numeric-phase groups first (sorted by int when
    # possible), then "Uncategorized" last.
    def _phase_sort_key(name: str) -> tuple[int, str]:
        m = re.search(r"\d+", name)
        if name == "Uncategorized":
            return (10_000, name)
        return (int(m.group()) if m else 9_999, name)

    for phase in sorted(groups.keys(), key=_phase_sort_key):
        lines.append(f"## {phase}" if phase != "Uncategorized" else "## Uncategorized")
        for t, display in groups[phase]:
            # `project_tasks` uses DEPLOYED as the terminal-success
            # state (see TaskStatus in src/models/base.py). FAILED /
            # CANCELLED stay unchecked — they're not "done" in the
            # checklist sense.
            done = t.task_status == TaskStatus.DEPLOYED
            box = "[x]" if done else "[ ]"
            meta_bits = [t.priority, t.task_type]
            if t.estimated_agent:
                meta_bits.append(t.estimated_agent)
            meta = " · ".join(meta_bits)
            lines.append(f"- {box} `{t.task_id}` · {display} ({meta})")
            if t.description:
                # Indent description lines by 6 spaces to nest under
                # the checklist item. Strip trailing whitespace per
                # line, preserve internal blank lines.
                for desc_line in t.description.splitlines():
                    lines.append(f"      {desc_line.rstrip()}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
