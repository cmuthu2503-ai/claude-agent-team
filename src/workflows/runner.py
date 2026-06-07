"""Workflow runner — executes stages with combined quality gate and rework loops."""

import asyncio
import re
from collections import defaultdict
from typing import Any, Callable, Protocol

import structlog

from src.workflows.loader import (
    ParallelStage,
    StageDefinition,
    WorkflowDefinition,
)

logger = structlog.get_logger()

MAX_REWORK_CYCLES = 2

# Seconds between successive parallel agents in a stage. Was 30s as a rate-limit
# defensive crutch; on Claude Platform on AWS / Opus 4.7 the agents take 60-120s
# each anyway, so a 3s offset keeps any "burst" effect small without making the
# UI look like only one agent is running.
PARALLEL_STAGGER_SECONDS = 3.0

# Affected-components routing — the PRD specialist emits a "**Affected Components:**
# frontend, backend" line in its output. The runner parses it after each stage and
# uses it to skip irrelevant parallel groups (e.g. skip backend_specialist on a
# pure UI theme task). If the line is missing or unparseable, all groups run
# (safe default — matches pre-Option-C behaviour).
_AFFECTED_COMPONENTS_RE = re.compile(
    r"\*?\*?Affected Components:\*?\*?\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_VALID_COMPONENTS: frozenset[str] = frozenset({"frontend", "backend"})


def _extract_affected_components(text: str) -> list[str] | None:
    """Parse an `**Affected Components:** frontend, backend` line from agent output.

    Returns the list of recognised components (subset of `_VALID_COMPONENTS`), or
    `None` if the marker is missing, malformed, or produced no recognised values.
    A `None` return tells the runner to fall back to running every parallel group.
    """
    if not text:
        return None
    match = _AFFECTED_COMPONENTS_RE.search(text)
    if not match:
        return None
    raw = match.group(1).strip()
    # Strip surrounding bracket characters that some agents emit despite the prompt format.
    raw = raw.strip("[](){}").strip()
    parts = re.split(r"[,;|]|\sand\s|\s+/\s+|\s+\+\s+", raw, flags=re.IGNORECASE)
    seen: list[str] = []
    for part in parts:
        token = part.strip().lower().strip(".,;:")
        if token in _VALID_COMPONENTS and token not in seen:
            seen.append(token)
    return seen or None


class AgentExecutor(Protocol):
    async def execute_agent(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]: ...


class WorkflowRunner:
    """Executes a workflow with a combined quality gate after Review + Testing."""

    def __init__(
        self, executor: AgentExecutor,
        code_commit_handler: Any = None,
        publish_handler: Any = None,
        materialize_handler: Any = None,
        # AET-06 — quality_guardian_approval gate enforcement.
        # Both optional for backward compat with existing tests that
        # construct WorkflowRunner without the AE plumbing. When either
        # is missing, _check_combined_gate falls back to the legacy
        # text-parse behavior (agent prose verdict only) and skips the
        # quality.gate.* event emission. Loud-fallback: structlog logs
        # `policy_check_unavailable` so the operator can spot the gap.
        get_policy_check_tool: Callable[[], Any] | None = None,
        events: Any = None,
        # AET-21 — `thresholds` is the parsed thresholds.yaml dict
        # (ConfigLoader.thresholds). Used to read
        # `security_max_severity_to_block` for the AE-4 security gate.
        # Optional for backward compat with existing tests that build
        # a runner without config — falls back to DEFAULT_MAX_SEVERITY.
        thresholds: dict[str, Any] | None = None,
    ) -> None:
        self.executor = executor
        self._code_commit_handler = code_commit_handler
        self._publish_handler = publish_handler
        self._get_policy_check_tool = get_policy_check_tool
        self._events = events
        self._thresholds = thresholds
        # Fix A: invoked RIGHT AFTER the development stage completes
        # (before review/testing). Writes the agent's ``### Full Source:``
        # blocks to disk + runs lint/test so the reviewer's file_read at
        # the review stage sees the agent's actual emission instead of
        # the scaffold. Returns dict with materialize_status; on failure
        # mirrors commit_status="failed" so the existing rework branch
        # catches it without a new state machine.
        self._materialize_handler = materialize_handler
        self._rework_count: dict[str, int] = {}

    async def run(
        self, workflow: WorkflowDefinition, request_id: str, initial_input: dict[str, Any],
        skip_stages: list[str] | None = None,
    ) -> dict[str, Any]:
        artifacts: dict[str, Any] = {**initial_input}
        execution_order = self._resolve_execution_order(workflow)
        self._rework_count[request_id] = 0
        _skip = set(skip_stages or [])

        i = 0
        while i < len(execution_order):
            stage_id = execution_order[i]

            # Skip stages with reused documents
            if stage_id in _skip:
                logger.info("workflow_stage_skipped_reused", stage=stage_id, request_id=request_id)
                i += 1
                continue

            stage = workflow.stages[stage_id]
            logger.info("workflow_stage_started", stage=stage_id, request_id=request_id)

            try:
                # code_commit is a system stage (agents: []) — handled by orchestrator
                if stage_id == "code_commit":
                    if self._code_commit_handler:
                        result = await self._code_commit_handler(request_id, artifacts)
                    else:
                        result = {}
                        logger.warning("no_code_commit_handler", request_id=request_id)
                # publish is a system stage for the research workflow — writes to docs/ + GitHub
                elif stage_id == "publish":
                    if self._publish_handler:
                        result = await self._publish_handler(request_id, artifacts)
                    else:
                        result = {}
                        logger.warning("no_publish_handler", request_id=request_id)
                elif isinstance(stage, ParallelStage):
                    result = await self._run_parallel_stage(stage, request_id, artifacts)
                else:
                    result = await self._run_stage(stage, request_id, artifacts)

                artifacts.update(result)
                logger.info("workflow_stage_completed", stage=stage_id, request_id=request_id)

                # Fix A — materialize hook. Fires AFTER the development
                # stage completes and BEFORE review starts, so the
                # reviewer's file_read sees the agent's actual emission
                # on disk (the architecture the reviewer's prompt
                # already assumes). On rework cycles, runs again with
                # the latest agent_outputs so any updated emissions
                # land on disk too. Failure here (drop guard, lint,
                # tsc) routes to rework via the SAME branch as
                # code_commit failures by mirroring commit_status.
                if (
                    stage_id == "development"
                    and self._materialize_handler is not None
                ):
                    mat_result = await self._materialize_handler(request_id, artifacts)
                    if isinstance(mat_result, dict):
                        # Stash materialized_files so _handle_code_commit
                        # can skip the re-parse + re-write.
                        if mat_result.get("materialize_status") == "success":
                            artifacts["materialized_files"] = mat_result.get(
                                "materialized_files", {}
                            )
                        # On materialize failure, fall through to the
                        # existing code_commit_failed rework branch by
                        # treating the result as if code_commit had run
                        # and failed. The branch below already knows
                        # how to inject rework_instructions and jump
                        # back to development.
                        if mat_result.get("commit_status") == "failed":
                            result = mat_result
                            stage_id_for_rework = "code_commit"  # reuse the branch
                        else:
                            stage_id_for_rework = stage_id
                    else:
                        stage_id_for_rework = stage_id
                else:
                    stage_id_for_rework = stage_id

                # Code-commit gate: if the orchestrator's commit handler returned
                # commit_status="failed", treat it like a quality-gate failure so
                # the existing rework machinery can recover. This catches the
                # late-stage failure modes (truncation, ruff, tsc) that would
                # otherwise kill the request after testing had already passed.
                if stage_id_for_rework == "code_commit" and isinstance(result, dict) and result.get("commit_status") == "failed":
                    rework_count = self._rework_count.get(request_id, 0)
                    commit_error = str(result.get("error", "Code commit failed"))
                    if rework_count < MAX_REWORK_CYCLES:
                        self._rework_count[request_id] = rework_count + 1
                        logger.warning(
                            "code_commit_failed_reworking",
                            request_id=request_id,
                            cycle=rework_count + 1,
                            max_cycles=MAX_REWORK_CYCLES,
                        )
                        artifacts["rework_instructions"] = (
                            f"REWORK REQUIRED (cycle {rework_count + 1}/{MAX_REWORK_CYCLES}). "
                            f"The code commit step REFUSED your previous output with the EXACT "
                            f"error shown below. You MUST address ONLY that error — apply the "
                            f"minimum change that resolves it. DO NOT:\n"
                            f"  - re-emit your full prior output\n"
                            f"  - add new content or 'helpful' edits to unrelated files\n"
                            f"  - touch files outside the original request's scope\n"
                            f"  - modify your own agent YAML (config/agents/**)\n"
                            f"If the error names a file and a line number, that single line "
                            f"is the only thing you need to fix. Use `search_replace` on that "
                            f"file with the smallest possible old_string → new_string diff.\n\n"
                            f"=== CODE COMMIT ERROR (FIX EXACTLY THIS) ===\n{commit_error}"
                        )
                        artifacts["rework_cycle"] = rework_count + 1
                        try:
                            dev_index = execution_order.index("development")
                            i = dev_index
                            continue
                        except ValueError:
                            # bug_fix uses 'fix' stage instead of 'development' — try that.
                            try:
                                dev_index = execution_order.index("fix")
                                i = dev_index
                                continue
                            except ValueError:
                                logger.error("rework_target_stage_not_found", request_id=request_id)
                    else:
                        logger.warning(
                            "code_commit_max_rework_cycles_reached",
                            request_id=request_id, cycles=rework_count,
                        )
                        artifacts["escalation_reason"] = (
                            f"Pipeline failed after {rework_count} rework cycles. "
                            f"Code commit was rejected — see error below.\n\n"
                            f"Last commit error:\n{commit_error[:1000]}"
                        )
                        artifacts["code_commit_error"] = commit_error
                        return artifacts

                # Security gate: runs after SECURITY stage
                if stage_id == "security":
                    sec_result = await self._check_security_gate(artifacts, request_id)
                    if not sec_result["passed"]:
                        rework_count = self._rework_count.get(request_id, 0)
                        if rework_count < MAX_REWORK_CYCLES:
                            self._rework_count[request_id] = rework_count + 1
                            logger.warning(
                                "security_gate_failed_reworking",
                                request_id=request_id,
                                cycle=rework_count + 1,
                            )
                            artifacts["rework_instructions"] = (
                                f"REWORK REQUIRED (cycle {rework_count + 1}/{MAX_REWORK_CYCLES}). "
                                f"Fix ALL security issues below before resubmitting:\n\n"
                                f"{sec_result['reason']}"
                            )
                            artifacts["rework_cycle"] = rework_count + 1
                            # Jump back to development/fix stage
                            for rework_target in ("development", "fix"):
                                try:
                                    i = execution_order.index(rework_target)
                                    break
                                except ValueError:
                                    continue
                            else:
                                logger.error("rework_target_stage_not_found", request_id=request_id)
                            continue
                        else:
                            logger.warning(
                                "security_gate_max_rework_reached",
                                request_id=request_id, cycles=rework_count,
                            )
                            artifacts["escalation_reason"] = (
                                f"Pipeline failed after {rework_count} rework cycles. "
                                f"Security gate could not pass.\n\n"
                                f"Last issues:\n{sec_result['reason'][:500]}"
                            )
                            return artifacts

                # Combined gate: runs after TESTING stage (checks both review + test)
                if stage_id == "testing":
                    gate_result = await self._check_combined_gate(artifacts, request_id)

                    if not gate_result["passed"]:
                        rework_count = self._rework_count.get(request_id, 0)

                        if rework_count < MAX_REWORK_CYCLES:
                            self._rework_count[request_id] = rework_count + 1
                            logger.warning(
                                "combined_gate_failed_reworking",
                                request_id=request_id,
                                cycle=rework_count + 1,
                                max_cycles=MAX_REWORK_CYCLES,
                            )

                            # Inject combined feedback for dev agents
                            artifacts["rework_instructions"] = (
                                f"REWORK REQUIRED (cycle {rework_count + 1}/{MAX_REWORK_CYCLES}). "
                                f"Fix ALL issues below:\n\n{gate_result['reason']}"
                            )
                            artifacts["rework_cycle"] = rework_count + 1

                            await self.executor.execute_agent(
                                "engineering_lead", request_id,
                                {"event": "rework_triggered", "cycle": rework_count + 1,
                                 "reason": gate_result["reason"][:200]}
                            ) if False else None  # placeholder for future notification

                            # Jump back to development stage
                            try:
                                dev_index = execution_order.index("development")
                                i = dev_index
                                continue
                            except ValueError:
                                logger.error("development_stage_not_found")
                        else:
                            # Max cycles reached — FAIL, do NOT run DevOps
                            logger.warning(
                                "max_rework_cycles_reached",
                                request_id=request_id, cycles=rework_count,
                            )
                            artifacts["escalation_reason"] = (
                                f"Pipeline failed after {rework_count} rework cycles. "
                                f"Both code review and testing could not pass.\n\n"
                                f"Last issues:\n{gate_result['reason'][:500]}"
                            )
                            # Skip remaining stages (deployment)
                            return artifacts

            except Exception:
                logger.exception("workflow_stage_failed", stage=stage_id, request_id=request_id)
                raise

            i += 1

        return artifacts

    async def _check_combined_gate(self, artifacts: dict[str, Any], request_id: str) -> dict[str, Any]:
        """Check Code Reviewer, Architecture Reviewer, Quality Guardian, AND Tester results.

        Pass = Review APPROVED + Arch APPROVED (no CRITICAL) + Quality APPROVED
               (both the agent's prose verdict AND the structured policy_check
               verdict per AET-06) + zero test FAILs.
        Fail = any has issues → aggregate feedback for targeted rework.

        Note: quality_report is produced during the review stage (parallel with code_reviewer
        and architecture_reviewer), so it is always available by the time this gate runs
        (after the testing stage).

        AET-06 addition: the structured policy_check tool now runs against
        the agent emissions in parallel with the agent's prose verdict.
        Either signal can fail the gate — gives us defense in depth (the
        agent's prose check catches cross-cutting issues like API contract
        drift; policy_check catches deterministic patterns from L11-L21
        without relying on the LLM's judgment). Emits the
        quality.gate.failed / quality.gate.passed events (AET-05) so the
        UI can surface the blocked state.
        """
        review_text = artifacts.get("review_report", "")
        if not review_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "code_reviewer" in key:
                    review_text = val
                    break

        arch_text = artifacts.get("arch_review_report", "")
        if not arch_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "architecture_reviewer" in key:
                    arch_text = val
                    break

        quality_text = artifacts.get("quality_report", "")
        if not quality_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "quality_guardian" in key:
                    quality_text = val
                    break

        tester_text = artifacts.get("tester_specialist_output", "")
        if not tester_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "tester" in key:
                    tester_text = val
                    break

        review_passed = self._check_review_passed(review_text)
        arch_passed = self._check_arch_review_passed(arch_text)
        quality_passed = self._check_quality_guardian_passed(quality_text)
        test_passed = self._check_tests_passed(tester_text)

        # AET-06 — structured policy_check evaluation. The tool reads
        # config/quality-rules.yaml (13 rules derived from L11-L21) and
        # returns a structured verdict that the gate enforces in
        # addition to the agent's text verdict.
        policy_result = await self._evaluate_policy_check_for_gate(
            artifacts, request_id,
        )
        policy_passed = policy_result["passed"]
        policy_feedback = policy_result.get("reason", "")

        if review_passed and arch_passed and quality_passed and test_passed and policy_passed:
            logger.info(
                "combined_gate_passed", request_id=request_id,
                policy_check_verdict=policy_result.get("verdict"),
            )
            return {
                "passed": True,
                "reason": "Code review, architecture review, quality guardian, policy_check, and testing all passed",
            }

        # Aggregate feedback
        feedback_parts = []
        if not review_passed:
            findings = self._extract_review_findings(review_text)
            feedback_parts.append(f"=== CODE REVIEW ISSUES ===\n{findings}")
        if not arch_passed:
            feedback_parts.append(
                f"=== ARCHITECTURE VIOLATIONS ===\n"
                f"The architecture_reviewer found CRITICAL violations that must be fixed "
                f"before this code can be committed. See the Architecture Review Report:\n\n"
                f"{arch_text[:2000] if arch_text else 'No arch review output found.'}"
            )
        if not quality_passed:
            feedback_parts.append(
                f"=== QUALITY GUARDIAN ESCALATION ===\n"
                f"The quality_guardian found CRITICAL cross-cutting issues (API contract "
                f"mismatches, missing test traceability, or known failure patterns) that "
                f"must be fixed before this code can be committed. "
                f"See the Quality Guardian Report:\n\n"
                f"{quality_text[:2000] if quality_text else 'No quality guardian report found.'}"
            )
        if not test_passed:
            failures = self._extract_test_failures(tester_text)
            feedback_parts.append(f"=== TEST FAILURES ===\n{failures}")
        if not policy_passed and policy_feedback:
            feedback_parts.append(policy_feedback)

        combined = "\n\n".join(feedback_parts)
        logger.info(
            "combined_gate_failed", request_id=request_id,
            review_passed=review_passed, arch_passed=arch_passed,
            quality_passed=quality_passed, test_passed=test_passed,
            policy_passed=policy_passed,
            policy_check_verdict=policy_result.get("verdict"),
        )
        return {"passed": False, "reason": combined}

    async def _evaluate_policy_check_for_gate(
        self, artifacts: dict[str, Any], request_id: str,
    ) -> dict[str, Any]:
        """Run policy_check against the agent emissions in this batch,
        emit the quality.gate.* event, return {passed, verdict, reason}.

        Returns ``passed=True`` (i.e. don't block on policy) when policy_check
        is unavailable — keeps the gate functional if the tool failed to
        register (e.g., a broken quality-rules.yaml). Loud-fallback: the
        ``policy_check_unavailable`` log line tells the operator the
        structured gate isn't running.
        """
        get_tool = self._get_policy_check_tool
        tool = get_tool() if get_tool is not None else None
        if tool is None:
            logger.info(
                "policy_check_unavailable",
                request_id=request_id,
                hint=(
                    "WorkflowRunner has no policy_check tool — gate falls "
                    "back to text-parse-only behavior. Check executor "
                    "boot logs for policy_check_registration_failed."
                ),
            )
            return {"passed": True, "verdict": "SKIPPED", "reason": ""}

        # Extract emissions from backend_code + frontend_code artifacts.
        # Reuse the same `### \`path\`` block regex the code_writer uses
        # to materialize files — that way the gate sees exactly the
        # files that will land on disk.
        emissions = self._extract_emissions_from_artifacts(artifacts)
        if not emissions:
            # No files emitted (e.g., research workflow); policy_check has
            # nothing to evaluate. Don't block.
            logger.debug(
                "policy_check_no_emissions",
                request_id=request_id,
                artifact_keys=list(artifacts.keys()),
            )
            return {"passed": True, "verdict": "PASS", "reason": ""}

        try:
            result = await tool.execute({"emissions": emissions})
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "policy_check_eval_failed",
                request_id=request_id, error=str(e),
            )
            # Don't block the workflow on a policy_check failure — same
            # rationale as the boot-time fallback. Loud log + advance.
            return {"passed": True, "verdict": "ERROR", "reason": ""}

        verdict = result.get("verdict", "PASS")
        violations = result.get("violations", [])
        summary = result.get("summary", {})

        # AET-05 — emit the quality.gate.* event so the UI (AET-07)
        # and audit log can surface the gate decision. Soft-fails so
        # an EventEmitter hiccup can't break the gate evaluation.
        if self._events is not None:
            try:
                from src.core.quality_gate import emit_quality_gate_event
                await emit_quality_gate_event(
                    self._events,
                    request_id=request_id,
                    verdict=verdict,
                    violations=violations,
                    summary=summary,
                    stage="code_review.quality_check",
                    rework_cycle=self._rework_count.get(request_id, 0),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "quality_gate_event_emit_failed",
                    request_id=request_id, error=str(e),
                )

        if verdict != "BLOCK":
            # PASS or PASS_WITH_WARNINGS — both let the workflow advance.
            return {"passed": True, "verdict": verdict, "reason": ""}

        # BLOCK — build a rework-feedback block from the enforce-severity
        # violations. Each entry carries the rule_id, file, snippet, and
        # the rule's own fix_hint so the next iteration of the agent
        # has actionable, line-by-line guidance.
        enforce_violations = [v for v in violations if v.get("severity") == "enforce"]
        warn_violations = [v for v in violations if v.get("severity") == "warn"]

        feedback_lines = [
            "=== POLICY CHECK BLOCKED ===",
            f"policy_check (the declarative rule catalog in config/quality-rules.yaml) "
            f"found {len(enforce_violations)} enforce-severity violation(s) and "
            f"{len(warn_violations)} warning(s). Enforce violations BLOCK the workflow — "
            f"the listed code must be fixed before this commit can advance.",
            "",
            "Violations to fix:",
        ]
        for i, v in enumerate(enforce_violations, start=1):
            rule_id = v.get("rule_id", "?")
            rule_name = v.get("rule_name", "")
            target = v.get("target_path", "?")
            snippet = (v.get("snippet") or "").strip()
            fix_hint = (v.get("fix_hint") or "").strip()
            lesson_ref = v.get("lesson_ref")
            lesson_str = f" (lesson {lesson_ref})" if lesson_ref else ""
            feedback_lines.append(
                f"\n{i}. [{rule_id}] {rule_name}{lesson_str}\n"
                f"   File: {target}\n"
                f"   Match: {snippet[:200]}\n"
                f"   Fix: {fix_hint}"
            )
        if warn_violations:
            feedback_lines.append(
                f"\n(plus {len(warn_violations)} warning(s) — not blocking, "
                f"but address them in this cycle if possible.)"
            )

        return {
            "passed": False,
            "verdict": "BLOCK",
            "reason": "\n".join(feedback_lines),
        }

    # File-block regex matches the materializer's parser exactly
    # (src/core/code_writer.py::_parse_and_write_files), so the gate
    # sees the same set of files that will land on disk if approved.
    _FILE_BLOCK_RE = re.compile(
        r'###\s+(?:Full Source:\s*)?`([^`]+)`\s*(?:\([^)]*\))?\s*\n```\w*\n([\s\S]*?)```'
    )

    # Which artifact keys hold code emissions from which agents. Order
    # matters only for logging — every key is scanned regardless.
    _CODE_ARTIFACT_KEYS = (
        ("backend_code", "backend_specialist"),
        ("frontend_code", "frontend_specialist"),
        ("backend_tests", "tester_specialist"),
        ("frontend_tests", "tester_specialist"),
    )

    def _extract_emissions_from_artifacts(
        self, artifacts: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Pull (target_path, content, agent_id, tool_name) emissions
        from the code-bearing artifact text blobs.

        Mirrors the materializer's file-block parser so the gate sees
        exactly the files that would land on disk at the commit stage.
        Skipping path-traversal patterns (`..`, leading `/`) matches the
        materializer's security guard.
        """
        emissions: list[dict[str, Any]] = []
        for key, agent_id in self._CODE_ARTIFACT_KEYS:
            text = artifacts.get(key, "")
            if not isinstance(text, str) or not text:
                continue
            for path, content in self._FILE_BLOCK_RE.findall(text):
                path = path.strip()
                content = content.strip()
                if not path or not content:
                    continue
                # Path-traversal guard mirroring the materializer.
                if ".." in path or path.startswith("/"):
                    continue
                emissions.append({
                    "target_path": path,
                    "content": content,
                    "agent_id": agent_id,
                    "tool_name": "file_write",
                    "rework_cycle": self._rework_count.get(
                        artifacts.get("request_id", ""), 0,
                    ),
                })
        return emissions

    def _check_arch_review_passed(self, text: str) -> bool:
        """Parse architecture_reviewer verdict.

        APPROVED  → pass (even if HIGH findings exist — those are warnings not blockers).
        ARCH_VIOLATION → fail (CRITICAL structural issues must be fixed before commit).
        No output → pass by default (agent may not have run yet).
        """
        if not text:
            return True  # No arch review output = pass by default
        upper = text.upper()
        if "ARCH_VIOLATION" in upper:
            return False
        if "**APPROVED**" in upper or "VERDICT**\n**APPROVED" in upper:
            return True
        # Secondary check: CRITICAL findings in the report = fail even without explicit verdict
        critical_count = upper.count("[CRITICAL]") + upper.count("**[CRITICAL]**")
        if critical_count > 0:
            return False
        return True  # No clear failure signal = pass

    def _check_quality_guardian_passed(self, text: str) -> bool:
        """Parse quality_guardian verdict for the quality_guardian_approval gate.

        APPROVED  → pass (risk low/medium + no CRITICAL findings).
        ESCALATED → fail (CRITICAL findings found).
        No output → pass by default (agent may not have run yet).
        """
        if not text:
            return True
        upper = text.upper()
        if "VERDICT: ESCALATED" in upper:
            return False
        if "VERDICT: APPROVED" in upper:
            return True
        # Secondary: any CRITICAL finding in the report → fail
        if "[CRITICAL]" in upper or "**[CRITICAL]**" in upper:
            return False
        return True  # No clear failure signal = pass

    async def _check_security_gate(
        self, artifacts: dict[str, Any], request_id: str,
    ) -> dict[str, Any]:
        """AET-21 — evaluate the security stage output against the
        configured ``security_max_severity_to_block`` threshold.

        Priority order:
          1. STRUCTURED PATH — the agent embedded a
             ```security-report-json fenced block. Parse findings,
             split via the AET-20 threshold, decide BLOCK/PASS
             mechanically. Emit security.gate.* event with the
             structured payload.
          2. PROSE FALLBACK — no JSON block, or it failed to parse.
             Fall back to the legacy verdict-line parser so older
             agents (or rework cycles where the model regressed to
             prose) still produce a sensible decision. Emit the same
             events but with empty `blocking` / `by_tool` lists so
             subscribers can still render something.

        Returns ``{"passed": bool, "reason": str}`` to match the
        existing call-site contract.
        """
        from src.core.security_gate import (
            emit_security_gate_event,
            evaluate_security_report,
        )
        from src.core.security_threshold import get_max_severity

        sec_text = artifacts.get("security_report", "")
        if not sec_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "security" in key:
                    sec_text = val
                    break

        max_severity = get_max_severity(self._thresholds)

        # ── 1. Structured path ────────────────────────────────────────
        decision = evaluate_security_report(sec_text, max_severity)
        if decision is not None:
            await emit_security_gate_event(self._events, request_id, decision)
            if decision["verdict"] == "PASS":
                logger.info(
                    "security_gate_passed_structured",
                    request_id=request_id,
                    finding_count=len(decision.get("non_blocking", [])),
                    max_severity=max_severity,
                )
                return {
                    "passed": True,
                    "reason": decision["summary"],
                }
            # BLOCK — build the rework feedback string with per-finding
            # detail so the next cycle has actionable guidance.
            feedback = [
                "=== SECURITY GATE BLOCKED (structured) ===",
                decision["summary"],
                "",
                "Findings at/above the configured cutoff "
                f"('{max_severity}'):",
            ]
            for f in decision["blocking"][:20]:  # cap to keep prompt sane
                feedback.append(
                    f"  - [{f.get('severity', '?').upper()}] "
                    f"{f.get('tool', '?')}/{f.get('rule_id', '?')} "
                    f"at {f.get('file', '?')}:{f.get('line', '?')} — "
                    f"{(f.get('message') or '')[:200]}"
                )
                if f.get("fix_hint"):
                    feedback.append(f"      Fix: {f['fix_hint'][:200]}")
            if decision["non_blocking"]:
                feedback.append("")
                feedback.append(
                    f"Sub-threshold findings (not blocking, but worth fixing): "
                    f"{len(decision['non_blocking'])}"
                )
            logger.info(
                "security_gate_failed_structured",
                request_id=request_id,
                blocking_count=len(decision["blocking"]),
                max_severity=max_severity,
            )
            return {"passed": False, "reason": "\n".join(feedback)}

        # ── 2. Prose fallback ─────────────────────────────────────────
        vuln_passed = self._check_no_critical_vulnerabilities(sec_text)
        secrets_passed = self._check_no_secrets_detected(sec_text)

        if vuln_passed and secrets_passed:
            logger.info(
                "security_gate_passed_prose_fallback", request_id=request_id,
            )
            await emit_security_gate_event(
                self._events, request_id,
                {
                    "verdict": "PASS", "max_severity": max_severity,
                    "blocking": [], "non_blocking": [], "by_tool": {},
                    "summary": "Security scan passed (prose fallback path).",
                },
            )
            return {
                "passed": True,
                "reason": "Security scan passed — no critical vulnerabilities or secrets detected",
            }

        feedback_parts = []
        if not vuln_passed:
            feedback_parts.append(
                "=== SECURITY VULNERABILITIES ===\n"
                "The security_specialist found CRITICAL or HIGH vulnerabilities that must be "
                "remediated before this code can be committed. See the Security Report:\n\n"
                f"{sec_text[:2000] if sec_text else 'No security report found.'}"
            )
        if not secrets_passed:
            feedback_parts.append(
                "=== SECRETS DETECTED ===\n"
                "Hard-coded credentials or secrets were found in the generated code. "
                "Remove all secrets and replace with environment variable references.\n\n"
                f"{sec_text[:1000] if sec_text else 'No security report found.'}"
            )

        logger.info(
            "security_gate_failed_prose_fallback", request_id=request_id,
            vuln_passed=vuln_passed, secrets_passed=secrets_passed,
        )
        await emit_security_gate_event(
            self._events, request_id,
            {
                "verdict": "BLOCK", "max_severity": max_severity,
                "blocking": [], "non_blocking": [], "by_tool": {},
                "summary": "Security scan failed (prose fallback path).",
            },
        )
        return {"passed": False, "reason": "\n\n".join(feedback_parts)}

    def _check_no_critical_vulnerabilities(self, text: str) -> bool:
        """Parse security_specialist verdict for the no_critical_vulnerabilities gate.

        PASS marker  → "Verdict: ✅ PASS"
        FAIL marker  → "Verdict: ❌ FAIL"
        No output    → pass by default (scanner may not have run)
        """
        if not text:
            return True
        upper = text.upper()
        if "VERDICT: ✅ PASS" in upper or "VERDICT:**  ✅ PASS" in upper or "VERDICT: ✅ PASS" in text:
            return True
        if "VERDICT: ❌ FAIL" in text or "VERDICT: ❌ FAIL" in upper:
            return False
        # Secondary: explicit CRITICAL/HIGH keyword in findings table
        if "[CRITICAL]" in upper or "**[CRITICAL]**" in upper:
            return False
        # Check for explicit FAIL line without emoji (safety fallback)
        if "VERDICT: FAIL" in upper:
            return False
        return True  # No clear failure signal

    def _check_no_secrets_detected(self, text: str) -> bool:
        """Parse security_specialist verdict for the no_secrets_detected gate.

        Fails only when the report explicitly mentions secrets found.
        """
        if not text:
            return True
        upper = text.upper()
        # Explicit FAIL from the whole scan (with or without bold markdown)
        if "❌ FAIL" in text or "VERDICT: FAIL" in upper:
            return False
        # Detect-secrets specific patterns
        if "SECRETS DETECTED" in upper and "0 SECRETS" not in upper:
            return False
        if "SECRETS_FOUND" in upper and "SECRETS_FOUND: 0" not in upper:
            return False
        return True

    def _check_review_passed(self, text: str) -> bool:
        if not text:
            return True  # No review output = pass by default
        upper = text.upper()
        if "**APPROVED**" in upper and "CRITICAL" not in upper:
            return True
        if "CHANGES REQUESTED" in upper or "NOT APPROVED" in upper:
            return False
        critical_count = upper.count("[CRITICAL]") + upper.count("**[CRITICAL]**")
        if critical_count > 0:
            return False
        return True  # No clear signal = pass

    def _check_tests_passed(self, text: str) -> bool:
        if not text:
            return True  # No test output = pass by default
        upper = text.upper()
        if "NEEDS FIXES" in upper or "NOT READY" in upper:
            return False
        # Count FAIL markers
        fail_count = upper.count("FAIL ❌") + upper.count("STATUS:** FAIL")
        if fail_count > 0:
            return False
        if "READY FOR DEPLOYMENT" in upper:
            return True
        return True  # No clear failures = pass

    def _extract_review_findings(self, text: str) -> str:
        lines = text.split("\n")
        findings = []
        in_findings = False
        for line in lines:
            if "### Findings" in line or "## Findings" in line:
                in_findings = True
                continue
            if in_findings:
                if line.startswith("### ") or line.startswith("## "):
                    break
                if line.strip():
                    findings.append(line)
        if findings:
            return "\n".join(findings[:30])
        # Fallback: look for CRITICAL/WARNING lines
        for line in lines:
            if "[CRITICAL]" in line or "[WARNING]" in line:
                findings.append(line.strip())
        return "\n".join(findings[:20]) if findings else text[:500]

    def _extract_test_failures(self, text: str) -> str:
        lines = text.split("\n")
        failures = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if "FAIL" in line and ("TC-" in line or "**TC-" in line):
                # Capture the test case name and a few lines of detail
                failures.append(line.strip())
                for j in range(1, 5):
                    if i + j < len(lines) and lines[i + j].strip().startswith("- **Reason"):
                        failures.append("  " + lines[i + j].strip())
                        break
            i += 1
        if failures:
            return "\n".join(failures[:20])
        # Fallback: find "Issues Found" section
        in_issues = False
        for line in lines:
            if "### Issues Found" in line:
                in_issues = True
                continue
            if in_issues:
                if line.startswith("### ") or line.startswith("## "):
                    break
                if line.strip():
                    failures.append(line.strip())
        if failures:
            return "\n".join(failures[:15])
        return "Test failures detected (details unavailable)"

    async def _run_stage(
        self, stage: StageDefinition, request_id: str, artifacts: dict[str, Any]
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for agent_id in stage.agents:
            result = await self.executor.execute_agent(agent_id, request_id, artifacts)
            results.update(result)
            # Capture an affected_components marker if this agent emitted one
            # (typically the PRD specialist). Persisted in the workflow artifacts
            # so downstream parallel stages can short-circuit irrelevant groups.
            components = _extract_affected_components(result.get("text", ""))
            if components is not None:
                results["affected_components"] = components
                logger.info(
                    "affected_components_extracted",
                    request_id=request_id,
                    agent=agent_id,
                    components=components,
                )
        return results

    async def _run_parallel_stage(
        self, stage: ParallelStage, request_id: str, artifacts: dict[str, Any]
    ) -> dict[str, Any]:
        affected = artifacts.get("affected_components")

        async def _staggered_execute(agent_id: str, inputs: dict, delay: float) -> dict[str, Any]:
            if delay > 0:
                await asyncio.sleep(delay)
            return await self.executor.execute_agent(agent_id, request_id, inputs)

        tasks: list[Any] = []
        delay = 0.0
        skipped: list[str] = []
        for group in stage.groups:
            # The affected-components filter only applies to groups whose `group_id`
            # is actually a domain name (frontend / backend). Other parallel stages
            # use group names like "review", "test" (bug_fix's review_and_test) or
            # "env", "test_plan" (demo_preparation's prepare) — those are stage-role
            # names, not domains, and must ALWAYS run regardless of what the PRD
            # listed as affected components.
            #
            # Without this check, bug_fix's review_and_test stage gets entirely
            # skipped because `review` and `test` don't appear in the components
            # list — bugs ship to production unreviewed and untested.
            is_domain_group = group.group_id in _VALID_COMPONENTS
            if affected and is_domain_group and group.group_id not in affected:
                skipped.append(group.group_id)
                logger.info(
                    "parallel_group_skipped",
                    request_id=request_id,
                    stage=stage.stage_id,
                    group=group.group_id,
                    affected=affected,
                )
                continue
            for agent_id in group.agents:
                tasks.append(_staggered_execute(agent_id, artifacts, delay))
                delay += PARALLEL_STAGGER_SECONDS

        if not tasks:
            # Every group got filtered out — log loudly and return empty so the
            # downstream review/testing stages see no new code to chew on.
            logger.warning(
                "parallel_stage_all_groups_skipped",
                request_id=request_id, stage=stage.stage_id, skipped=skipped,
            )
            return {}

        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        combined: dict[str, Any] = {}
        for result in results_list:
            if isinstance(result, Exception):
                logger.warning("parallel_agent_exception", error=str(result))
                continue
            combined.update(result)
        return combined

    def _resolve_execution_order(self, workflow: WorkflowDefinition) -> list[str]:
        graph: dict[str, list[str]] = defaultdict(list)
        all_stages = set(workflow.stages.keys())
        has_incoming: set[str] = set()

        for stage_id, stage in workflow.stages.items():
            next_stages = stage.next_stages if hasattr(stage, "next_stages") else []
            for next_id in next_stages:
                graph[stage_id].append(next_id)
                has_incoming.add(next_id)

        start_nodes = [s for s in all_stages if s not in has_incoming]
        if not start_nodes:
            return list(workflow.stages.keys())

        order: list[str] = []
        queue = list(start_nodes)
        visited: set[str] = set()
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            order.append(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        for stage_id in all_stages:
            if stage_id not in order:
                order.append(stage_id)

        return order
