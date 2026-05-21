"""Drift computation for the AI Deploy Judge (per-project).

A project has "drift" when its working code has moved past the commit
the running container was built from. The Deploy panel's "X commits
since last deploy" section is rendered from the output of this module.

Single public entry point: ``compute_drift(state, project)`` returns
a ``ProjectDrift`` describing exactly what changed and which commit
range the next judge call should evaluate.

Why a module instead of inlining in the route:
  - The judge LLM module (Phase 3) needs the same drift snapshot as
    input. Centralizing the computation keeps the snapshot identical
    in both call sites.
  - The route handler (Phase 4) wants a small, mockable helper for
    tests — passing a fake StateStore + Project in is enough to
    exercise every branch.
  - Phase 7 (frontend) renders the same drift_summary that lands in
    the deploy_decisions row. Keeping the schema in one place
    prevents drift between "what the judge saw" and "what the UI
    shows you it saw".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.models.base import Project
from src.state.base import StateStore


# Hard cap on the number of commits we feed into the judge. A 50-commit
# build cycle is fine to summarise; beyond that the prompt bloats. If
# the user has more than 50 commits of drift, recommend rebuild-all
# without consulting the judge (it'd say that anyway).
_MAX_COMMITS_FOR_JUDGE = 50


@dataclass
class ProjectDrift:
    """Snapshot of what's changed since the project last successfully
    deployed. Empty (``commits == []``) when there's no drift — the UI
    renders the "Up to date" state and skips the judge call entirely."""

    project_id: str
    # All committed Requests since the baseline, oldest-first. Each dict
    # has the keys documented on SqliteStateStore.list_commits_since_deploy:
    # request_id, commit_sha, description, files, file_count, completed_at.
    commits: list[dict[str, Any]] = field(default_factory=list)
    # The commit the running container was built from (NULL if never
    # deployed). The judge uses this + the latest commit_sha below to
    # name the range it's evaluating.
    from_commit_sha: str | None = None
    # The newest commit's SHA — the target the judge is recommending an
    # action for. Empty when there's no drift.
    to_commit_sha: str | None = None
    # Union of files touched across all commits in the drift window.
    # Lets the judge see the aggregate footprint without re-counting.
    files_touched: list[str] = field(default_factory=list)
    # When True, the drift exceeds _MAX_COMMITS_FOR_JUDGE — skip the
    # LLM and default to a `rebuild-all` recommendation with a clear
    # reasoning string. Cost guard: a 200-commit drift would otherwise
    # cost a $0.10+ judge call that just says "rebuild everything".
    over_limit: bool = False

    @property
    def has_drift(self) -> bool:
        return len(self.commits) > 0

    @property
    def commit_count(self) -> int:
        return len(self.commits)


async def compute_drift(
    state: StateStore,
    project: Project,
) -> ProjectDrift:
    """Compute the drift between the project's running deploy and its
    latest committed code.

    The "baseline" is whichever is more authoritative:
      1. ``project.deploy_last_started_at`` — when the running container
         was last (re)deployed. Anything completed AFTER this is drift.
      2. NULL → no deploy yet, every committed Request counts as drift.

    We deliberately do NOT use ``last_deploy_commit_sha`` as the cutoff
    because it's a SHA, not a timestamp, and "commits since SHA X" would
    require either a git log walk or a stored ordinal — both of which
    are heavier than necessary. The deploy time is already in the
    project row.
    """
    since: datetime | None = project.deploy_last_started_at
    commits = await state.list_commits_since_deploy(project.project_id, since=since)

    drift = ProjectDrift(project_id=project.project_id)
    if not commits:
        return drift

    drift.commits = commits[:_MAX_COMMITS_FOR_JUDGE]
    drift.over_limit = len(commits) > _MAX_COMMITS_FOR_JUDGE
    drift.from_commit_sha = project.last_deploy_commit_sha
    drift.to_commit_sha = commits[-1]["commit_sha"]
    # Union of files touched, deduped, stable order (preserves first
    # appearance). The judge cares more about "what tiers got touched"
    # than the exact ordering, but a stable list is friendlier for the UI.
    seen: set[str] = set()
    union: list[str] = []
    for c in drift.commits:
        for f in c.get("files", []) or []:
            if f not in seen:
                seen.add(f)
                union.append(f)
    drift.files_touched = union
    return drift
