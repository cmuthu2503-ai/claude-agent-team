"""Orchestrator — main entry point. Manages request lifecycle end-to-end."""

import uuid
from datetime import datetime
from typing import Any

import structlog

from src.config.loader import ConfigLoader
from src.core.aggregator import Aggregator
from src.core.dispatcher import Dispatcher
from src.core.events import EventEmitter
from src.models.base import Request, RequestStatus, Subtask, SubtaskStatus
from src.state.base import StateStore
from src.workflows.loader import WorkflowLoader
from src.workflows.runner import AgentExecutor, WorkflowRunner

logger = structlog.get_logger()


class Orchestrator(AgentExecutor):
    """Top-level coordinator. Submits requests, runs workflows, aggregates results."""

    def __init__(
        self,
        config: ConfigLoader,
        state: StateStore,
        events: EventEmitter,
    ) -> None:
        self.config = config
        self.state = state
        self.events = events
        self.dispatcher = Dispatcher(config)
        self.aggregator = Aggregator()
        self.workflow_loader = WorkflowLoader(config)
        self.workflow_loader.load_all()
        self.runner = WorkflowRunner(executor=self)
        self._agent_executor: Any = None  # Set by agent system in Phase 3

    def set_agent_executor(self, executor: Any) -> None:
        """Inject the real agent executor (set during Phase 3 agent system init)."""
        self._agent_executor = executor

    async def submit(self, description: str, task_type: str = "feature_request",
                     priority: str = "medium", created_by: str = "") -> Request:
        request_id = f"REQ-{uuid.uuid4().hex[:6].upper()}"
        request = Request(
            request_id=request_id,
            description=description,
            task_type=task_type,
            priority=priority,
            created_by=created_by,
        )
        await self.state.create_request(request)
        await self.events.emit("request.created", {
            "request_id": request_id, "description": description, "task_type": task_type,
        })
        logger.info("request_submitted", request_id=request_id, task_type=task_type)

        # Execute workflow asynchronously
        try:
            request.status = RequestStatus.ANALYZING
            await self.state.update_request(request)

            workflow = self.workflow_loader.get_workflow_for_trigger(task_type)
            if not workflow:
                raise ValueError(f"No workflow found for trigger: {task_type}")

            result = await self.runner.run(
                workflow, request_id, {"description": description}
            )

            request.status = RequestStatus.COMPLETED
            request.completed_at = datetime.utcnow()
            await self.state.update_request(request)
            await self.events.emit("request.completed", {
                "request_id": request_id, "result": result,
            })

        except Exception as e:
            logger.exception("request_failed", request_id=request_id)
            request.status = RequestStatus.FAILED
            await self.state.update_request(request)
            await self.events.emit("request.failed", {
                "request_id": request_id, "error": str(e),
            })

        return request

    async def execute_agent(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single agent task. Called by WorkflowRunner."""
        import uuid as _uuid
        subtask_id = f"{request_id}-{agent_id.upper()}-{_uuid.uuid4().hex[:4]}"
        subtask = Subtask(
            subtask_id=subtask_id,
            request_id=request_id,
            agent_id=agent_id,
        )
        await self.state.create_subtask(subtask)
        await self.events.emit("agent.started", {
            "request_id": request_id, "agent_id": agent_id, "subtask_id": subtask_id,
        })

        try:
            subtask.status = SubtaskStatus.IN_PROGRESS
            subtask.started_at = datetime.utcnow()
            await self.state.update_subtask(subtask)

            # Use real agent executor if available, otherwise return mock result
            if self._agent_executor:
                result = await self._agent_executor.execute(agent_id, request_id, inputs)
            else:
                result = {"outputs": {}, "artifacts": [], "status": "completed"}
                logger.warning("using_mock_executor", agent_id=agent_id)

            subtask.status = SubtaskStatus.COMPLETED
            subtask.completed_at = datetime.utcnow()
            subtask.output_artifacts = result.get("artifacts", [])
            await self.state.update_subtask(subtask)
            await self.events.emit("agent.completed", {
                "request_id": request_id, "agent_id": agent_id,
                "subtask_id": subtask_id, "artifacts": subtask.output_artifacts,
            })
            return result

        except Exception as e:
            subtask.status = SubtaskStatus.FAILED
            subtask.completed_at = datetime.utcnow()
            subtask.error_message = str(e)
            await self.state.update_subtask(subtask)
            await self.events.emit("agent.failed", {
                "request_id": request_id, "agent_id": agent_id, "error": str(e),
            })
            return {"status": "failed", "error": str(e), "outputs": {}, "artifacts": []}
