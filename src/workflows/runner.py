"""Workflow runner — executes stages with combined quality gate and rework loops."""

import asyncio
from collections import defaultdict
from typing import Any, Protocol

import structlog

from src.workflows.loader import (
    ParallelStage,
    StageDefinition,
    WorkflowDefinition,
)

logger = structlog.get_logger()

MAX_REWORK_CYCLES = 2


class AgentExecutor(Protocol):
    async def execute_agent(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]: ...


class WorkflowRunner:
    """Executes a workflow with a combined quality gate after Review + Testing."""

    def __init__(self, executor: AgentExecutor) -> None:
        self.executor = executor
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
                if isinstance(stage, ParallelStage):
                    result = await self._run_parallel_stage(stage, request_id, artifacts)
                else:
                    result = await self._run_stage(stage, request_id, artifacts)

                artifacts.update(result)
                logger.info("workflow_stage_completed", stage=stage_id, request_id=request_id)

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
        """Check BOTH Code Reviewer verdict AND Tester results.

        Pass = Review APPROVED + zero test FAILs.
        Fail = either has issues → aggregate feedback.
        """
        review_text = artifacts.get("review_report", "")
        if not review_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "code_reviewer" in key:
                    review_text = val
                    break

        tester_text = artifacts.get("tester_specialist_output", "")
        if not tester_text:
            for key, val in artifacts.items():
                if isinstance(val, str) and "tester" in key:
                    tester_text = val
                    break

        review_passed = self._check_review_passed(review_text)
        test_passed = self._check_tests_passed(tester_text)

        if review_passed and test_passed:
            logger.info("combined_gate_passed", request_id=request_id)
            return {"passed": True, "reason": "Both code review and testing passed"}

        # Aggregate feedback
        feedback_parts = []
        if not review_passed:
            findings = self._extract_review_findings(review_text)
            feedback_parts.append(f"=== CODE REVIEW ISSUES ===\n{findings}")
        if not test_passed:
            failures = self._extract_test_failures(tester_text)
            feedback_parts.append(f"=== TEST FAILURES ===\n{failures}")

        combined = "\n\n".join(feedback_parts)
        logger.info(
            "combined_gate_failed", request_id=request_id,
            review_passed=review_passed, test_passed=test_passed,
        )
        return {"passed": False, "reason": combined}

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
        return "\n".join(failures[:15]) if failures else "Test failures detected (details unavailable)"

    async def _run_stage(
        self, stage: StageDefinition, request_id: str, artifacts: dict[str, Any]
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for agent_id in stage.agents:
            result = await self.executor.execute_agent(agent_id, request_id, artifacts)
            results.update(result)
        return results

    async def _run_parallel_stage(
        self, stage: ParallelStage, request_id: str, artifacts: dict[str, Any]
    ) -> dict[str, Any]:
        async def _staggered_execute(agent_id: str, inputs: dict, delay: float) -> dict[str, Any]:
            if delay > 0:
                await asyncio.sleep(delay)
            return await self.executor.execute_agent(agent_id, request_id, inputs)

        tasks = []
        delay = 0.0
        for group in stage.groups:
            for agent_id in group.agents:
                tasks.append(_staggered_execute(agent_id, artifacts, delay))
                delay += 30.0

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
