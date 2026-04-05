"""Metrics recorder — tracks agent execution traces and system metrics."""

import uuid
from datetime import datetime
from typing import Any

import structlog

from src.models.base import AgentTrace, Metric
from src.state.base import StateStore

logger = structlog.get_logger()


class MetricsRecorder:
    """Records execution metrics and agent traces."""

    def __init__(self, state: StateStore) -> None:
        self.state = state

    async def start_agent_trace(
        self, trace_id: str, request_id: str, agent_id: str, subtask_id: str,
    ) -> AgentTrace:
        trace = AgentTrace(
            trace_id=trace_id,
            request_id=request_id,
            agent_id=agent_id,
            subtask_id=subtask_id,
            status="running",
        )
        await self.state.record_agent_trace(trace)
        return trace

    async def complete_agent_trace(
        self, trace: AgentTrace, llm_calls: int = 0, tool_calls: int = 0,
        input_tokens: int = 0, output_tokens: int = 0, error: str | None = None,
    ) -> None:
        now = datetime.utcnow()
        trace.completed_at = now
        trace.duration_ms = int((now - trace.started_at).total_seconds() * 1000)
        trace.llm_calls = llm_calls
        trace.tool_calls = tool_calls
        trace.input_tokens = input_tokens
        trace.output_tokens = output_tokens
        trace.status = "failed" if error else "completed"
        trace.error_message = error
        await self.state.update_agent_trace(trace)

    async def record(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        metric = Metric(
            metric_id=str(uuid.uuid4()),
            metric_name=name,
            metric_value=value,
            labels=labels or {},
        )
        await self.state.record_metric(metric)
