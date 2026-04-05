"""Workflow loader — parses workflows.yaml into executable stage objects."""

from dataclasses import dataclass, field
from typing import Any

from src.config.loader import ConfigLoader


@dataclass
class QualityGate:
    gate: str
    threshold: str = ""
    required: bool = False
    stage: str = ""


@dataclass
class StageDefinition:
    stage_id: str
    agents: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    next_stages: list[str] = field(default_factory=list)
    quality_gates: list[QualityGate] = field(default_factory=list)
    on_fail: str | None = None
    routing: str | None = None
    sub_stages: list[str] = field(default_factory=list)  # for deployment stages


@dataclass
class ParallelGroup:
    group_id: str
    agents: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


@dataclass
class ParallelStage:
    stage_id: str
    groups: list[ParallelGroup] = field(default_factory=list)
    next_stages: list[str] = field(default_factory=list)
    quality_gates: list[QualityGate] = field(default_factory=list)
    on_fail: str | None = None


@dataclass
class WorkflowDefinition:
    workflow_id: str
    description: str
    trigger: str
    stages: dict[str, StageDefinition | ParallelStage] = field(default_factory=dict)


class WorkflowLoader:
    """Loads workflow definitions from workflows.yaml."""

    def __init__(self, config_loader: ConfigLoader) -> None:
        self.config_loader = config_loader
        self._workflows: dict[str, WorkflowDefinition] = {}

    def load_all(self) -> dict[str, WorkflowDefinition]:
        raw = self.config_loader.workflows.get("workflows", {})
        for wf_id, wf_config in raw.items():
            self._workflows[wf_id] = self._parse_workflow(wf_id, wf_config)
        return self._workflows

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        return self._workflows.get(workflow_id)

    def get_workflow_for_trigger(self, trigger: str) -> WorkflowDefinition | None:
        for wf in self._workflows.values():
            if wf.trigger == trigger:
                return wf
        return None

    def _parse_workflow(self, wf_id: str, config: dict[str, Any]) -> WorkflowDefinition:
        stages: dict[str, StageDefinition | ParallelStage] = {}
        for stage_id, stage_config in config.get("stages", {}).items():
            if "parallel" in stage_config:
                stages[stage_id] = self._parse_parallel_stage(stage_id, stage_config)
            else:
                stages[stage_id] = self._parse_stage(stage_id, stage_config)
        return WorkflowDefinition(
            workflow_id=wf_id,
            description=config.get("description", ""),
            trigger=config.get("trigger", ""),
            stages=stages,
        )

    def _parse_stage(self, stage_id: str, config: dict[str, Any]) -> StageDefinition:
        gates = [
            QualityGate(
                gate=g.get("gate", ""),
                threshold=g.get("threshold", ""),
                required=g.get("required", False),
                stage=g.get("stage", ""),
            )
            for g in config.get("quality_gates", [])
        ]
        return StageDefinition(
            stage_id=stage_id,
            agents=config.get("agents", []),
            inputs=config.get("inputs", []),
            outputs=config.get("outputs", []),
            next_stages=config.get("next", []),
            quality_gates=gates,
            on_fail=config.get("on_fail"),
            routing=config.get("routing"),
            sub_stages=config.get("stages", []),
        )

    def _parse_parallel_stage(self, stage_id: str, config: dict[str, Any]) -> ParallelStage:
        groups = []
        for group_id, group_config in config.get("parallel", {}).items():
            groups.append(ParallelGroup(
                group_id=group_id,
                agents=group_config.get("agents", []),
                inputs=group_config.get("inputs", []),
                outputs=group_config.get("outputs", []),
            ))
        gates = [
            QualityGate(
                gate=g.get("gate", ""),
                threshold=g.get("threshold", ""),
                required=g.get("required", False),
                stage=g.get("stage", ""),
            )
            for g in config.get("quality_gates", [])
        ]
        return ParallelStage(
            stage_id=stage_id,
            groups=groups,
            next_stages=config.get("next", []),
            quality_gates=gates,
            on_fail=config.get("on_fail"),
        )
