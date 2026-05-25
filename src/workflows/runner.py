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
    ) -> None:
        self.executor = executor
        self._code_commit_handler = code_commit_handler
        self._publish_handler = publish_handler
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
                    sec_result = self._check_security_gate(artifacts, request_id)
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
                    gate_result = self._check_combined_gate(artifacts, request_id)

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

    def _check_combined_gate(self, artifacts: dict[str, Any], request_id: str) -> dict[str, Any]:
        """Check Code Reviewer, Architecture Reviewer, Quality Guardian, AND Tester results.

        Pass = Review APPROVED + Arch APPROVED (no CRITICAL) + Quality APPROVED + zero test FAILs.
        Fail = any has issues → aggregate feedback for targeted rework.

        Note: quality_report is produced during the review stage (parallel with code_reviewer
        and architecture_reviewer), so it is always available by the time this gate runs
        (after the testing stage).
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

        if review_passed and arch_passed and quality_passed and test_passed:
            logger.info("combined_gate_passed", request_id=request_id)
            return {
                "passed": True,
                "reason": "Code review, architecture review, quality guardian, and testing all passed",
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

        combined = "\n\n".join(feedback_parts)
        logger.info(
            "combined_gate_failed", request_id=request_id,
            review_passed=review_passed, arch_passed=arch_passed,
            quality_passed=quality_passed, test_passed=test_passed,
        )
        return {"passed": False, "reason": combined}

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

    def _check_security_gate(self, artifacts: dict[str, Any], request_id: str) -> dict[str, Any]:
        """Evaluate the security stage output against no_critical_vulnerabilities
        and no_secrets_detected gates.

        Pass = security_specialist emitted "Verdict: ✅ PASS" (or no report yet).
        Fail = "Verdict: ❌ FAIL" or explicit CRITICAL/HIGH findings present.
        """
        sec_text = artifacts.get("security_report", "")
        if not sec_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "security" in key:
                    sec_text = val
                    break

        vuln_passed = self._check_no_critical_vulnerabilities(sec_text)
        secrets_passed = self._check_no_secrets_detected(sec_text)

        if vuln_passed and secrets_passed:
            logger.info("security_gate_passed", request_id=request_id)
            return {"passed": True, "reason": "Security scan passed — no critical vulnerabilities or secrets detected"}

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
            "security_gate_failed", request_id=request_id,
            vuln_passed=vuln_passed, secrets_passed=secrets_passed,
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
