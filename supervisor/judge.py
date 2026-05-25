"""Deployment judge — LLM-driven strategy decision for the supervisor.

The supervisor calls `evaluate_deployment()` before it runs any docker commands.
The judge looks at what was committed and returns a structured decision:

    {
      "strategy":  "deploy_full" | "deploy_staging_only" | "skip" | "hold",
      "risk":      "low" | "medium" | "high",
      "reasoning": "<2-4 sentence explanation>",
      "rollback_plan": "<what to do if this goes sideways>"
    }

The supervisor then executes a deterministic flow based on `strategy`. The LLM
NEVER calls docker / git directly — it only decides. This is the "hybrid"
pattern: agent for judgment, Python for execution.

Falls back to a safe default (deploy_full, risk=medium, reasoning="judge
unavailable") if Anthropic is unreachable, so the supervisor still works
when the LLM is down.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---- Public types ----------------------------------------------------------

VALID_STRATEGIES = ("deploy_full", "deploy_staging_only", "skip", "hold")
VALID_RISKS = ("low", "medium", "high")


@dataclass
class JudgeResult:
    """Structured output the supervisor reads to branch its deploy flow."""
    strategy: str          # one of VALID_STRATEGIES
    risk: str              # one of VALID_RISKS
    reasoning: str         # human-readable, 2-4 sentences
    rollback_plan: str = ""
    # True iff the judge produced this via an actual LLM call. False when we
    # fell back to a safe default (LLM unreachable / parse failed / no creds).
    from_llm: bool = field(default=False)


# ---- Prompt ----------------------------------------------------------------

_SYSTEM_PROMPT = """You are the deployment judge. You decide WHAT deployment
strategy a given commit should follow, but you do NOT execute the deployment
yourself — a deterministic script runs the actual docker commands based on
your decision.

Given a commit's metadata, return EXACTLY one valid JSON object matching this schema:

{
  "strategy":  "deploy_full" | "deploy_staging_only" | "skip" | "hold",
  "risk":      "low" | "medium" | "high",
  "reasoning": "<2-4 sentences explaining your decision>",
  "rollback_plan": "<one sentence describing how to undo if this fails>"
}

Strategy meanings:
  - "deploy_full":
      build → staging → healthcheck → rebuild dev. Normal path.
  - "deploy_staging_only":
      build → staging → healthcheck → STOP. Use for risky changes needing manual
      validation before prod.
  - "skip":
      no docker work. Use when nothing committed actually needs deploying
      (docs-only, .gitignore, comment-only diffs).
  - "hold":
      do not deploy; row stays in 'on_hold' until manual intervention. Use for
      clearly problematic commits (critical-file deletions, no tests, off-hours
      risky deploys).

Risk meanings:
  - "low":    small, isolated change with clear test coverage.
              e.g. one CSS rule, one new component.
  - "medium": multi-file change touching existing logic, but reviewed and tested.
  - "high":   large blast radius (auth, payment, schema migrations, infra),
              insufficient tests, or unfamiliar areas.

QUALITY GUARDIAN RISK SIGNAL (`quality_risk` in the user message):
The quality_risk field is the Quality Guardian's cross-cutting pre-deploy
assessment of the change (API contract correctness, test traceability,
known failure-pattern compliance). Use it to calibrate your decision:

  - "high"  → Quality Guardian ESCALATED (CRITICAL findings: API mismatch,
              missing traceability, or known repeat patterns).
              Prefer "deploy_staging_only". Only use "hold" if files_committed
              also touch auth / schema migrations / infra.
  - "medium" → Quality Guardian APPROVED with HIGH warnings.
              Normal risk; "deploy_full" is appropriate.
  - "low"   → Quality Guardian fully APPROVED — no findings above MEDIUM.
              You may lower your risk rating one level below your file-shape
              assessment (e.g. a multi-file change you'd call medium → low).
  - "unknown" → quality_guardian did not run (e.g. docs-only request).
              Ignore this field and judge on file shape alone.

Output ONLY the JSON object. No preamble, no markdown fences, no explanation outside the JSON.
"""


_USER_TEMPLATE = """Commit to evaluate:

- commit_sha:       {commit_sha}
- request_id:       {request_id}
- files_committed:  {files_list}
- rollback_sha:     {rollback_sha}
- quality_risk:     {quality_risk}  (Quality Guardian rating: low / medium / high / unknown)

Reason about the change shape and return the JSON decision."""


# ---- Main entry point ------------------------------------------------------

def evaluate_deployment(
    *,
    commit_sha: str,
    request_id: str,
    files_committed: list[str],
    rollback_sha: str = "",
    quality_risk: str = "unknown",
    model: str = "claude-opus-4-7",
) -> JudgeResult:
    """Ask the judge LLM what to do with this commit.

    quality_risk: the Quality Guardian's rating ("low" | "medium" | "high" |
        "unknown"). "unknown" is used when the quality guardian did not run
        (e.g. docs-only requests). Injected into the user prompt so the judge
        can calibrate strategy — "high" biases toward deploy_staging_only,
        "low" biases toward lowering the overall risk rating.

    Always returns a JudgeResult — never raises. On any failure (no creds,
    API error, malformed response), returns a safe-default result with
    `from_llm=False` so the supervisor still proceeds with a sensible default.
    """
    api_key = os.environ.get("ANTHROPIC_AWS_API_KEY", "").strip()
    workspace_id = os.environ.get("ANTHROPIC_AWS_WORKSPACE_ID", "").strip()
    region = (
        os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    ).strip()
    inference_geo = os.environ.get("ANTHROPIC_AWS_INFERENCE_GEO", "us").strip() or None

    if not api_key or not workspace_id:
        logger.warning(
            "judge_skipped_no_credentials",
        )
        return _safe_default("LLM credentials missing — proceeding with default deploy_full.")

    try:
        from anthropic import AnthropicAWS  # sync client for the sync supervisor
    except Exception as e:
        logger.warning("judge_skipped_sdk_unavailable", extra={"error": str(e)})
        return _safe_default("Anthropic SDK unavailable in supervisor image.")

    files_list = ", ".join(files_committed) if files_committed else "(none reported)"
    # Validate/normalise quality_risk so the prompt always gets a clean value
    _valid_qr = {"low", "medium", "high", "unknown"}
    quality_risk_clean = quality_risk.strip().lower() if quality_risk.strip().lower() in _valid_qr else "unknown"
    user_msg = _USER_TEMPLATE.format(
        commit_sha=commit_sha or "(none)",
        request_id=request_id or "(unknown)",
        files_list=files_list,
        rollback_sha=rollback_sha or "(none)",
        quality_risk=quality_risk_clean,
    )

    try:
        client = AnthropicAWS(api_key=api_key, workspace_id=workspace_id, aws_region=region)
        kwargs = {
            "model": model,
            "max_tokens": 1024,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_msg}],
        }
        if inference_geo:
            kwargs["inference_geo"] = inference_geo

        response = client.messages.create(**kwargs)
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
    except Exception as e:
        logger.exception("judge_llm_call_failed")
        return _safe_default(f"LLM call failed: {e!r} — proceeding with default deploy_full.")

    return _parse_response(text)


# ---- Helpers ---------------------------------------------------------------

def _safe_default(reason: str) -> JudgeResult:
    """When the judge can't run, default to a normal deploy_full at medium risk."""
    return JudgeResult(
        strategy="deploy_full",
        risk="medium",
        reasoning=reason,
        rollback_plan="git revert HEAD && redeploy",
        from_llm=False,
    )


def _parse_response(text: str) -> JudgeResult:
    """Extract the JSON object from the LLM's reply, validate it, and return
    a JudgeResult. On any parse or validation failure, log + safe-default."""
    if not text:
        return _safe_default("LLM returned an empty response.")

    # Strip ```json fences the LLM sometimes adds despite the prompt saying no.
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
        logger.warning("judge_response_unparseable", extra={"error": str(e), "text": cleaned[:200]})
        return _safe_default(f"LLM returned non-JSON: {cleaned[:120]}")

    strategy = (data.get("strategy") or "").strip().lower()
    risk = (data.get("risk") or "").strip().lower()
    reasoning = (data.get("reasoning") or "").strip()
    rollback_plan = (data.get("rollback_plan") or "").strip()

    if strategy not in VALID_STRATEGIES:
        logger.warning("judge_invalid_strategy", extra={"got": strategy})
        return _safe_default(f"LLM picked unknown strategy {strategy!r}; using deploy_full.")
    if risk not in VALID_RISKS:
        # Tolerable — coerce to medium and keep the rest of the decision.
        risk = "medium"

    return JudgeResult(
        strategy=strategy,
        risk=risk,
        reasoning=reasoning or "(no reasoning provided)",
        rollback_plan=rollback_plan,
        from_llm=True,
    )
