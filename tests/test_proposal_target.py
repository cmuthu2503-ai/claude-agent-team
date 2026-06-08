"""HAI-59 (FR-035e) — target-integrity validation at confirm time."""

import pytest

from src.core.proposal_target import validate_proposal_target
from src.models.base import Project, ProjectStatus, Proposal


class _FakeState:
    def __init__(self, project: Project | None = None, raise_on_get: bool = False):
        self._project = project
        self._raise = raise_on_get

    async def get_project(self, pid: str):
        if self._raise:
            raise RuntimeError("db down")
        return self._project if (self._project and self._project.project_id == pid) else None


def _prop(action_type="prd.generate", target_ref="proj-1") -> Proposal:
    return Proposal(
        proposal_id="p1", action_type=action_type, target_ref=target_ref, proposed_by="service:hermes"
    )


async def test_active_project_passes():
    state = _FakeState(Project(project_id="proj-1", name="Atlas", status=ProjectStatus.ACTIVE))
    assert await validate_proposal_target(state, _prop()) is None


async def test_archived_project_fails_with_reason():
    state = _FakeState(Project(project_id="proj-1", name="Atlas", status=ProjectStatus.ARCHIVED))
    reason = await validate_proposal_target(state, _prop())
    assert reason is not None and "archived" in reason


async def test_deleted_project_fails_with_reason():
    state = _FakeState(project=None)                        # get_project → None
    reason = await validate_proposal_target(state, _prop())
    assert reason is not None and "no longer exists" in reason


async def test_empty_target_ref_for_project_action_fails():
    state = _FakeState(Project(project_id="proj-1", name="Atlas"))
    reason = await validate_proposal_target(state, _prop(target_ref=None))
    assert reason is not None and "target project" in reason


async def test_non_project_action_passes_through():
    # deploy isn't project-target-validated here; returns None regardless of state.
    state = _FakeState(project=None)
    assert await validate_proposal_target(state, _prop(action_type="deploy", target_ref="env:prod")) is None


async def test_lookup_error_is_fail_open():
    # A DB blip must NOT block a human-confirmed action — fail open (None).
    state = _FakeState(raise_on_get=True)
    assert await validate_proposal_target(state, _prop()) is None


@pytest.mark.parametrize(
    "action_type",
    ["project.brief.set", "prd.generate", "apispec.generate", "epics.generate",
     "features.generate", "tasks.generate", "buildplan.generate"],
)
async def test_all_project_actions_are_validated(action_type):
    state = _FakeState(Project(project_id="proj-1", name="Atlas", status=ProjectStatus.ARCHIVED))
    assert await validate_proposal_target(state, _prop(action_type=action_type)) is not None
