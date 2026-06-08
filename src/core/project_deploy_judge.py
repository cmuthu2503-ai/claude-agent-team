"""Per-project AI Deploy Judge — LLM-driven action selection for the
project's running app.

The backend calls ``evaluate_project_deploy()`` whenever there's
drift between the project's last deployed commit and its current
HEAD. The judge looks at what changed (file list per commit + the
task descriptions that drove each commit) and returns a structured
``ProjectJudgeResult``:

    {
      "action":     <one of DeployAction>,
      "risk":       "low" | "medium" | "high",
      "confidence": "low" | "medium" | "high",
      "reasoning":  "<1-3 sentences>",
    }

The supervisor (Phase 5) then executes a deterministic docker
flow based on ``action``. The LLM NEVER runs docker / git itself
— same "hybrid" pattern as ``supervisor/judge.py``: agent for
judgment, Python for execution.

Always returns a result — never raises. Safe default is
``rebuild-all`` at medium risk + medium confidence, used whenever
the LLM is unreachable, returns malformed JSON, or the drift is
too large to summarise economically.

Mirrors ``supervisor/judge.py`` deliberately so the two judges
stay structurally aligned and easy to compare. Differences:
  - Async (backend calls this, not the sync supervisor)
  - 7 actions, not 4 strategies
  - Adds ``confidence`` so the UI can render the "primary CTA"
    pulse only when low risk + high confidence
  - Accepts ``preferences`` and ``prior_overrides`` so Phase 8's
    learning loop has somewhere to land
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.core.deploy_drift import ProjectDrift
from src.models.base import DeployAction, DeployDecision, DeployRiskLevel, Project

logger = structlog.get_logger()


# ---- Public types ----------------------------------------------------------

VALID_ACTIONS: tuple[str, ...] = tuple(str(a) for a in DeployAction)
VALID_RISKS: tuple[str, ...] = tuple(str(r) for r in DeployRiskLevel)


@dataclass
class ProjectJudgeResult:
    """Structured output the route handler persists into ``deploy_decisions``."""
    action: str            # one of VALID_ACTIONS
    risk: str              # one of VALID_RISKS
    confidence: str        # one of VALID_RISKS (reuses low/medium/high)
    reasoning: str         # 1-3 sentences, human-readable
    # True iff the judge produced this via an actual LLM call. False
    # when we fell back to a safe default (no creds, parse failed,
    # drift over limit, etc.). Surfaced to the UI as a small "default"
    # badge so the user knows when judgment was bypassed.
    from_llm: bool = field(default=False)


# ---- Prompt ----------------------------------------------------------------

_SYSTEM_PROMPT = """You are the per-project AI Deploy Judge. You decide WHAT
docker-compose action a project's accumulated commits should trigger, but you
do NOT run docker yourself — a deterministic supervisor executes the action
based on your decision.

Given a project's drift (the commits that landed since its last deploy),
return EXACTLY one valid JSON object matching this schema:

{
  "action":     "skip" | "restart-backend" | "restart-frontend" |
                "rebuild-backend" | "rebuild-frontend" | "rebuild-all" |
                "hold",
  "risk":       "low" | "medium" | "high",
  "confidence": "low" | "medium" | "high",
  "reasoning":  "<1-3 sentences explaining your choice>"
}

Action meanings (cheapest first):
  - "skip":              no docker work. Use for docs-only / .gitignore /
                         comment-only changes; advance the deploy baseline.
  - "restart-backend":   `docker compose restart backend`. Use when ONLY
                         backend Python source changed and there's no new
                         dependency (no requirements.txt, no Dockerfile
                         changes). Cheapest backend refresh — uvicorn
                         --reload picks up the new source on container
                         restart, no rebuild needed.
  - "restart-frontend":  `docker compose restart frontend`. Same logic for
                         frontend source changes (.tsx, .ts, .css, etc.)
                         when package.json / Dockerfile unchanged.
  - "rebuild-backend":   `docker compose up -d --build backend`. Use when
                         requirements.txt or backend Dockerfile changed.
                         More expensive (~30-60s) but necessary for new deps.
  - "rebuild-frontend":  `docker compose up -d --build frontend`. Use when
                         package.json or frontend Dockerfile changed.
  - "rebuild-all":       `docker compose up -d --build`. Use when
                         docker-compose.yml or other cross-cutting infra
                         changed, OR when the change spans both tiers AND
                         touches dependencies.
  - "hold":              no action. Use ONLY for clearly risky changes that
                         need human review BEFORE any docker work — schema
                         migrations without backups, auth/permission rewrites,
                         large blast-radius changes with no test coverage.

Risk meanings:
  - "low":    isolated change with clear test coverage. Backend route added,
              CSS tweak, new component file.
  - "medium": multi-file change touching existing logic, dependencies bumped,
              modest blast radius.
  - "high":   schema migrations, auth/permission edits, large refactors,
              cross-cutting infra changes.

Confidence meanings:
  - "high":   the change is obviously within one tier and has unambiguous
              action (e.g. only .py files in src/api/, nothing else).
  - "medium": the change has multiple plausible actions but one is clearly
              best (e.g. backend code + a small docs update → restart-backend).
  - "low":    ambiguous (e.g. agent-generated files of unknown shape,
              cross-tier change without clear dependency footprint).

Output ONLY the JSON object. No preamble, no markdown fences, no explanation
outside the JSON. Reasoning goes inside the "reasoning" key, not before/after.
"""


# User-facing template. {commits_json} is a JSON array of
# {commit_sha, description, files, file_count, completed_at}; the
# LLM reads it directly rather than us pre-prosing.
_USER_TEMPLATE = """Project to evaluate:

- project_name:     {name}
- project_kind:     {kind}
- commit_range:     {from_sha}..{to_sha}
- commits_count:    {commits_count}

Drift (commits since last deploy, oldest-first):
{commits_json}

{preferences_block}{overrides_block}Reason about the change shape and return the JSON decision."""


# ---- Main entry point ------------------------------------------------------

async def evaluate_project_deploy(
    *,
    project: Project,
    drift: ProjectDrift,
    prior_overrides: list[DeployDecision] | None = None,
    model: str = "claude-opus-4-8",
) -> ProjectJudgeResult:
    """Ask the judge LLM what to do with the project's drift.

    Never raises — every failure path returns a safe default with
    ``from_llm=False``. The route handler is allowed to use the result
    unconditionally.

    Cost guards:
      - No drift → ``skip`` with ``from_llm=False``, no LLM call.
      - ``drift.over_limit`` → ``rebuild-all``, no LLM call.
      - Missing creds → safe default, no LLM call.

    Otherwise: one Anthropic call (~1024 max_tokens, ~$0.005 each).
    """
    # ── Drift-shape shortcuts (no LLM needed) ──
    if not drift.has_drift:
        return _safe_default(
            action=DeployAction.SKIP,
            reason="No drift since last deploy — nothing to do.",
        )
    if drift.over_limit:
        return _safe_default(
            action=DeployAction.REBUILD_ALL,
            risk=DeployRiskLevel.MEDIUM,
            reason=(
                f"{drift.commit_count}+ commits since last deploy exceeds the "
                "judge cost cap; defaulting to rebuild-all rather than burn "
                "an LLM call on a foregone conclusion."
            ),
        )

    # ── Creds gate ──
    api_key = os.environ.get("ANTHROPIC_AWS_API_KEY", "").strip()
    workspace_id = os.environ.get("ANTHROPIC_AWS_WORKSPACE_ID", "").strip()
    region = (
        os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    ).strip()
    inference_geo = os.environ.get("ANTHROPIC_AWS_INFERENCE_GEO", "us").strip() or None

    if not api_key or not workspace_id:
        logger.warning("project_deploy_judge_no_credentials", project_id=project.project_id)
        return _safe_default(
            action=DeployAction.REBUILD_ALL,
            risk=DeployRiskLevel.MEDIUM,
            reason="LLM credentials missing — defaulting to rebuild-all.",
        )

    # ── SDK gate ──
    try:
        from anthropic import AsyncAnthropicAWS  # async client for the async backend
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("project_deploy_judge_sdk_unavailable", error=str(e))
        return _safe_default(
            action=DeployAction.REBUILD_ALL,
            risk=DeployRiskLevel.MEDIUM,
            reason="Anthropic SDK unavailable; defaulting to rebuild-all.",
        )

    # ── Build the user message ──
    user_msg = _render_user_message(project=project, drift=drift, prior_overrides=prior_overrides)

    # ── LLM call ──
    try:
        client = AsyncAnthropicAWS(api_key=api_key, workspace_id=workspace_id, aws_region=region)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 1024,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_msg}],
        }
        if inference_geo:
            kwargs["inference_geo"] = inference_geo
        response = await client.messages.create(**kwargs)
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
    except Exception as e:
        logger.exception(
            "project_deploy_judge_llm_call_failed",
            project_id=project.project_id, err=str(e),
        )
        return _safe_default(
            action=DeployAction.REBUILD_ALL,
            risk=DeployRiskLevel.MEDIUM,
            reason=f"LLM call failed ({type(e).__name__}); defaulting to rebuild-all.",
        )

    return _parse_response(text)


# ---- Helpers ---------------------------------------------------------------

def _render_user_message(
    *,
    project: Project,
    drift: ProjectDrift,
    prior_overrides: list[DeployDecision] | None,
) -> str:
    """Build the user-facing prompt. Truncates file lists per-commit to
    keep token usage bounded — full diff hunks are a stretch goal."""
    # Cap per-commit file lists at 20 entries; aggregate file count
    # captures the total without spamming the prompt.
    commits_payload: list[dict[str, Any]] = []
    for c in drift.commits:
        files = c.get("files", []) or []
        commits_payload.append({
            "commit_sha": c.get("commit_sha", "")[:8],
            "description": c.get("description", "")[:200],
            "files": files[:20] + (["…"] if len(files) > 20 else []),
            "file_count": c.get("file_count", len(files)),
            "completed_at": c.get("completed_at", ""),
        })

    preferences_block = ""
    prefs = (project.deploy_judge_preferences or "").strip()
    if prefs:
        preferences_block = (
            "Project-specific preferences (user-authored, factor these in):\n"
            f"  {prefs}\n\n"
        )

    overrides_block = ""
    if prior_overrides:
        # Surface the last N (recommended, overridden) pairs so the
        # judge can adjust to this user's quirks. Keep it short — the
        # prompt should bias, not memorise.
        lines: list[str] = []
        for d in prior_overrides[:5]:
            actual = d.overridden_action or "(unknown)"
            lines.append(f"  - You suggested {d.action!s}; user chose {actual!s} instead.")
        overrides_block = (
            "Recent overrides (this user's stated preferences):\n"
            + "\n".join(lines)
            + "\n\n"
        )

    return _USER_TEMPLATE.format(
        name=project.name,
        kind=str(project.kind),
        from_sha=(drift.from_commit_sha or "(never deployed)")[:8],
        to_sha=(drift.to_commit_sha or "(unknown)")[:8],
        commits_count=drift.commit_count,
        commits_json=json.dumps(commits_payload, indent=2),
        preferences_block=preferences_block,
        overrides_block=overrides_block,
    )


def _safe_default(
    *,
    action: DeployAction,
    risk: DeployRiskLevel = DeployRiskLevel.LOW,
    reason: str = "Default — judge bypassed.",
) -> ProjectJudgeResult:
    """Construct a result that didn't come from the LLM. ``from_llm=False``
    so the UI can render a subtle "default" badge."""
    return ProjectJudgeResult(
        action=str(action),
        risk=str(risk),
        confidence=str(DeployRiskLevel.MEDIUM),
        reasoning=reason,
        from_llm=False,
    )


def _parse_response(text: str) -> ProjectJudgeResult:
    """Extract the JSON object from the LLM's reply, validate it,
    coerce stragglers (unknown risk → medium), and return a
    ProjectJudgeResult. On any structural failure, log + safe-default."""
    if not text:
        return _safe_default(
            action=DeployAction.REBUILD_ALL,
            risk=DeployRiskLevel.MEDIUM,
            reason="LLM returned an empty response; defaulting to rebuild-all.",
        )

    # Strip ```json fences the LLM sometimes adds despite the prompt
    # explicitly saying no markdown.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[\w]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # Greedy match for a JSON object if there's any preamble text.
    if not cleaned.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            cleaned = m.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(
            "project_deploy_judge_response_unparseable",
            err=str(e), text=cleaned[:200],
        )
        return _safe_default(
            action=DeployAction.REBUILD_ALL,
            risk=DeployRiskLevel.MEDIUM,
            reason=f"LLM returned non-JSON ({str(e)[:80]}); defaulting to rebuild-all.",
        )

    action = (data.get("action") or "").strip().lower()
    risk = (data.get("risk") or "").strip().lower()
    confidence = (data.get("confidence") or "").strip().lower()
    reasoning = (data.get("reasoning") or "").strip()

    if action not in VALID_ACTIONS:
        logger.warning("project_deploy_judge_invalid_action", got=action)
        return _safe_default(
            action=DeployAction.REBUILD_ALL,
            risk=DeployRiskLevel.MEDIUM,
            reason=(
                f"LLM picked unknown action {action!r}; defaulting to rebuild-all."
            ),
        )
    if risk not in VALID_RISKS:
        risk = "medium"  # tolerable — coerce
    if confidence not in VALID_RISKS:
        confidence = "medium"  # tolerable — coerce

    return ProjectJudgeResult(
        action=action,
        risk=risk,
        confidence=confidence,
        reasoning=reasoning or "(no reasoning provided)",
        from_llm=True,
    )
