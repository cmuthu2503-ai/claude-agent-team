"""Workflow runner — executes workflow stages with parallel support and quality gates."""

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


class AgentExecutor(Protocol):
    """Protocol for executing agent tasks. Implemented by the orchestrator."""

    async def execute_agent(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]: ...


class QualityGateEvaluator(Protocol):
    """Protocol for evaluating quality gates."""

    async def evaluate(
        self, gate_id: str, threshold: str, context: dict[str, Any]
    ) -> bool: ...


class WorkflowRunner:
    """Executes a workflow definition stage by stage."""

    def __init__(
        self,
        executor: AgentExecutor,
        gate_evaluator: QualityGateEvaluator | None = None,
    ) -> None:
        self.executor = executor
        self.gate_evaluator = gate_evaluator

    async def run(
        self, workflow: WorkflowDefinition, request_id: str, initial_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a workflow from start to finish, returning all collected artifacts."""
        artifacts: dict[str, Any] = {**initial_input}
        execution_order = self._resolve_execution_order(workflow)

        for stage_id in execution_order:
            stage = workflow.stages[stage_id]
            logger.info("workflow_stage_started", stage=stage_id, request_id=request_id)

            try:
                if isinstance(stage, ParallelStage):
                    result = await self._run_parallel_stage(stage, request_id, artifacts)
                else:
                    result = await self._run_stage(stage, request_id, artifacts)

                artifacts.update(result)

                # Evaluate quality gates
                gates = stage.quality_gates if hasattr(stage, "quality_gates") else []
                for gate in gates:
                    passed = await self._evaluate_gate(gate.gate, gate.threshold, artifacts)
                    if not passed:
                        on_fail = stage.on_fail if hasattr(stage, "on_fail") else None
                        if on_fail:
                            logger.warning(
                                "quality_gate_failed",
                                gate=gate.gate, stage=stage_id,
                                routing_to=on_fail, request_id=request_id,
                            )
                            # Re-run from the on_fail stage
                            return await self._run_from_stage(
                                workflow, request_id, artifacts, on_fail
                            )
                        raise RuntimeError(
                            f"Quality gate '{gate.gate}' failed at stage '{stage_id}' with no on_fail route"
                        )

                logger.info("workflow_stage_completed", stage=stage_id, request_id=request_id)

            except Exception:
                logger.exception("workflow_stage_failed", stage=stage_id, request_id=request_id)
                raise

        return artifacts

    async def _run_stage(
        self, stage: StageDefinition, request_id: str, artifacts: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a sequential stage — run each agent and collect results."""
        stage_inputs = {k: artifacts[k] for k in stage.inputs if k in artifacts}
        results: dict[str, Any] = {}

        for agent_id in stage.agents:
            result = await self.executor.execute_agent(agent_id, request_id, stage_inputs)
            results.update(result)

        return results

    async def _run_parallel_stage(
        self, stage: ParallelStage, request_id: str, artifacts: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute parallel groups concurrently via asyncio.gather."""
        tasks = []
        for group in stage.groups:
            group_inputs = {k: artifacts[k] for k in group.inputs if k in artifacts}
            for agent_id in group.agents:
                tasks.append(
                    self.executor.execute_agent(agent_id, request_id, group_inputs)
                )

        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        combined: dict[str, Any] = {}
        for result in results_list:
            if isinstance(result, Exception):
                raise result
            combined.update(result)
        return combined

    async def _evaluate_gate(
        self, gate_id: str, threshold: str, context: dict[str, Any]
    ) -> bool:
        """Evaluate a quality gate. Returns True if passed."""
        if self.gate_evaluator:
            return await self.gate_evaluator.evaluate(gate_id, threshold, context)
        # Default: pass if no evaluator configured
        return True

    async def _run_from_stage(
        self, workflow: WorkflowDefinition, request_id: str,
        artifacts: dict[str, Any], start_stage: str,
    ) -> dict[str, Any]:
        """Re-run workflow starting from a specific stage (for on_fail routing)."""
        execution_order = self._resolve_execution_order(workflow)
        try:
            start_idx = execution_order.index(start_stage)
        except ValueError:
            raise RuntimeError(f"on_fail stage '{start_stage}' not found in workflow")

        for stage_id in execution_order[start_idx:]:
            stage = workflow.stages[stage_id]
            if isinstance(stage, ParallelStage):
                result = await self._run_parallel_stage(stage, request_id, artifacts)
            else:
                result = await self._run_stage(stage, request_id, artifacts)
            artifacts.update(result)

        return artifacts

    def _resolve_execution_order(self, workflow: WorkflowDefinition) -> list[str]:
        """Topological sort of stages based on 'next' declarations."""
        # Build adjacency list from next_stages
        graph: dict[str, list[str]] = defaultdict(list)
        all_stages = set(workflow.stages.keys())
        has_incoming: set[str] = set()

        for stage_id, stage in workflow.stages.items():
            next_stages = stage.next_stages if hasattr(stage, "next_stages") else []
            for next_id in next_stages:
                graph[stage_id].append(next_id)
                has_incoming.add(next_id)

        # Find start nodes (no incoming edges)
        start_nodes = [s for s in all_stages if s not in has_incoming]
        if not start_nodes:
            # Fallback: use dict order
            return list(workflow.stages.keys())

        # BFS topological sort
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

        # Add any stages not reachable (shouldn't happen in valid config)
        for stage_id in all_stages:
            if stage_id not in order:
                order.append(stage_id)

        return order
