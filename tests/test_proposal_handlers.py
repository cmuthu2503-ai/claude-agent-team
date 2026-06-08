"""P3 (HAI-36..39) — gated-action handlers execute via the real route functions."""

import pytest

from src.api.routes import projects as projects_mod
from src.core import proposal_handlers as ph
from src.core.proposal_dispatcher import run_confirmed_proposal
from src.core.proposal_registry import GATED_ACTION_TYPES, ProposalActionRegistry
from src.models.base import (
    ArtifactKind,
    Project,
    ProjectStatus,
    Proposal,
    ProposalStatus,
    TaskStatus,
)
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "ph.db"))
    await s.initialize()
    yield s
    await s.close()


class _Ctx:
    """Minimal stand-in for the FastAPI Request the dispatcher passes as ctx —
    route functions only touch request.app.state.state_store here."""

    def __init__(self, store):
        self.app = type("A", (), {"state": type("S", (), {"state_store": store})()})()


def _reg() -> ProposalActionRegistry:
    r = ProposalActionRegistry()
    ph.register_all(r)
    return r


def _confirmed(action_type, target_ref=None, payload=None) -> Proposal:
    return Proposal(
        proposal_id="p1", action_type=action_type, target_ref=target_ref,
        payload=payload or {}, proposed_by="service:hermes", status=ProposalStatus.CONFIRMED,
    )


# ── registration ─────────────────────────────────────────────────────────────

def test_register_all_only_registers_gated_actions():
    reg = ProposalActionRegistry()
    ph.register_all(reg)
    registered = set(reg.registered_action_types())
    assert {"project.create", "project.brief.set", "prd.generate", "apispec.generate"} <= registered
    # every handler we register must be a real gated action_type
    assert registered <= GATED_ACTION_TYPES


# ── end-to-end through the dispatcher (real route, real store) ───────────────

async def test_brief_set_runs_real_route_end_to_end(store):
    await store.create_project(Project(project_id="proj-1", name="Atlas", status=ProjectStatus.ACTIVE))
    reg = ProposalActionRegistry()
    ph.register_all(reg)
    proposal = _confirmed("project.brief.set", target_ref="proj-1", payload={"content": "x" * 120})

    out = await run_confirmed_proposal(proposal, reg, ctx=_Ctx(store))
    assert out["status"] == "executed"
    assert out["result_ref"]                                   # an artifact id
    # the brief artifact really landed in the store
    art = await store.get_artifact("proj-1", ArtifactKind.BRIEF)
    assert art is not None and art.content == "x" * 120


async def test_brief_set_too_short_fails_cleanly(store):
    await store.create_project(Project(project_id="proj-1", name="Atlas"))
    reg = ProposalActionRegistry()
    ph.register_all(reg)
    proposal = _confirmed("project.brief.set", target_ref="proj-1", payload={"content": "short"})

    out = await run_confirmed_proposal(proposal, reg, ctx=_Ctx(store))
    assert out["status"] == "failed"
    assert "at least" in out["error"]                          # route's 400 detail, readable


async def test_brief_set_missing_project_fails_cleanly(store):
    reg = ProposalActionRegistry()
    ph.register_all(reg)
    proposal = _confirmed("project.brief.set", target_ref="ghost", payload={"content": "x" * 120})
    out = await run_confirmed_proposal(proposal, reg, ctx=_Ctx(store))
    assert out["status"] == "failed"


# ── payload → route mapping (monkeypatched routes, no LLM/network) ───────────

async def test_project_create_defaults_create_repo_off(monkeypatch):
    seen = {}

    async def fake_create_project(*, body, request, user):
        seen["create_repo"] = body.create_repo
        seen["name"] = body.name
        seen["role"] = user["role"]
        return {"data": {"project_id": "proj-new"}}

    monkeypatch.setattr(projects_mod, "create_project", fake_create_project)
    proposal = _confirmed("project.create", payload={"name": "Atlas"})
    out = await ph.project_create(proposal, ctx=None)
    assert out["result_ref"] == "proj-new"
    assert seen["create_repo"] is False                        # no surprise GitHub repo
    assert seen["name"] == "Atlas"
    assert seen["role"] == "admin"                             # system principal


async def test_prd_generate_passes_target_and_body(monkeypatch):
    seen = {}

    async def fake_generate_prd(*, project_id, request, body, user):
        seen["project_id"] = project_id
        return {"data": {"artifact_id": "prd-9"}}

    monkeypatch.setattr(projects_mod, "generate_prd", fake_generate_prd)
    proposal = _confirmed("prd.generate", target_ref="proj-1", payload={})
    out = await ph.prd_generate(proposal, ctx=None)
    assert out["result_ref"] == "prd-9" and seen["project_id"] == "proj-1"


# ── HAI-40 — build-plan family ───────────────────────────────────────────────

async def test_buildplan_family_registered():
    reg = ProposalActionRegistry()
    ph.register_all(reg)
    assert {"epics.generate", "features.generate", "tasks.generate", "buildplan.generate"} <= set(
        reg.registered_action_types()
    )


async def test_features_generate_threads_epic_id_from_payload(monkeypatch):
    seen = {}

    async def fake_generate_features(*, project_id, epic_id, request, body, user):
        seen.update(project_id=project_id, epic_id=epic_id)
        return {"data": {"artifact_id": "feat-list-1"}}

    monkeypatch.setattr(projects_mod, "generate_features", fake_generate_features)
    proposal = _confirmed("features.generate", target_ref="proj-1", payload={"epic_id": "epic-7"})
    out = await ph.features_generate(proposal, ctx=None)
    assert out["result_ref"] == "feat-list-1"
    assert seen["project_id"] == "proj-1" and seen["epic_id"] == "epic-7"


async def test_epics_and_buildplan_target_the_project(monkeypatch):
    async def fake_epics(*, project_id, request, body, user):
        return {"data": {"artifact_id": "epics-1"}}

    async def fake_bp(*, project_id, request, body, user):
        return {"data": {"id": "bp-1"}}

    monkeypatch.setattr(projects_mod, "generate_epics", fake_epics)
    monkeypatch.setattr(projects_mod, "generate_build_plan", fake_bp)
    assert (await ph.epics_generate(_confirmed("epics.generate", "proj-1"), None))["result_ref"] == "epics-1"
    assert (await ph.buildplan_generate(_confirmed("buildplan.generate", "proj-1"), None))["result_ref"] == "bp-1"


# ── HAI-41 — task.dispatch ───────────────────────────────────────────────────

class _Task:
    def __init__(self, tid):
        self.task_id = tid
        self.description = f"do {tid}"
        self.title = tid
        self.task_type = "feature_request"
        self.priority = "medium"


class _Req:
    def __init__(self, rid):
        self.request_id = rid


class _DispatchCtx:
    """ctx with a fake orchestrator + state recording dispatch calls."""

    def __init__(self, tasks):
        self._tasks = {t.task_id: t for t in tasks}
        self.set_status = []
        self.submitted = []
        outer = self

        class _State:
            async def get_task(self, tid):
                return outer._tasks.get(tid)

            async def set_task_status(self, tid, status, request_id=None):
                outer.set_status.append((tid, status, request_id))

        class _Orch:
            async def submit(self, **kw):
                outer.submitted.append(kw)
                return _Req(f"REQ-{len(outer.submitted)}")

        self.app = type("A", (), {"state": type("S", (), {
            "state_store": _State(), "orchestrator": _Orch(),
        })()})()


async def test_task_dispatch_submits_and_marks_dispatched():
    ctx = _DispatchCtx([_Task("t1"), _Task("t2")])
    proposal = _confirmed("task.dispatch", target_ref="proj-1", payload={"task_ids": ["t1", "t2"]})
    out = await ph.task_dispatch(proposal, ctx)
    assert out["result_ref"] == "REQ-1,REQ-2"
    assert len(ctx.submitted) == 2
    assert ctx.submitted[0]["project_id"] == "proj-1" and ctx.submitted[0]["source_task_id"] == "t1"
    assert [s[1] for s in ctx.set_status] == [TaskStatus.DISPATCHED, TaskStatus.DISPATCHED]


async def test_task_dispatch_no_tasks_fails():
    ctx = _DispatchCtx([])
    proposal = _confirmed("task.dispatch", target_ref="proj-1", payload={"task_ids": ["ghost"]})
    out = await run_confirmed_proposal(proposal, _reg(), ctx)
    assert out["status"] == "failed" and "no dispatchable" in out["error"]


# ── HAI-42 — deploy / rollback ───────────────────────────────────────────────

async def test_rollback_enqueues_and_is_idempotent(store):
    reg = ProposalActionRegistry()
    ph.register_all(reg)
    p = _confirmed("rollback", target_ref="env:staging", payload={"env": "staging", "deploy_id": "d1"})

    out1 = await run_confirmed_proposal(p, reg, ctx=_Ctx(store))
    assert out1["status"] == "executed" and out1["result_ref"].startswith("rb-")
    queued = await store.get_in_flight_rollback_for_env("staging")
    assert queued is not None and queued.env == "staging"

    # second confirmed rollback for the same env → returns the SAME row (idempotent)
    out2 = await run_confirmed_proposal(p, reg, ctx=_Ctx(store))
    assert out2["result_ref"] == out1["result_ref"]
    assert len(await store.list_pending_rollback_requests()) == 1


async def test_rollback_requires_env(store):
    reg = ProposalActionRegistry()
    ph.register_all(reg)
    p = _confirmed("rollback", payload={})
    out = await run_confirmed_proposal(p, reg, ctx=_Ctx(store))
    assert out["status"] == "failed" and "env" in out["error"]


async def test_deploy_invokes_route(monkeypatch):
    seen = {}

    async def fake_deploy(*, project_id, request, user):
        seen["project_id"] = project_id
        return {"data": {"status": "pending_deploy"}}

    monkeypatch.setattr(projects_mod, "deploy_project", fake_deploy)
    out = await ph.ops_deploy(_confirmed("deploy", target_ref="proj-1"), ctx=None)
    assert out["result_ref"] == "proj-1" and seen["project_id"] == "proj-1"


# ── HAI-33/34/35 — request.submit ────────────────────────────────────────────

class _SubmitCtx:
    def __init__(self):
        self.calls = []
        outer = self

        class _Orch:
            async def submit(self, **kw):
                outer.calls.append(kw)
                return _Req("REQ-NEW")

        self.app = type("A", (), {"state": type("S", (), {"orchestrator": _Orch()})()})()


async def test_request_submit_uses_inferred_project(monkeypatch):
    ctx = _SubmitCtx()
    proposal = _confirmed(
        "request.submit", payload={"description": "build X", "project_id": "proj-7", "rationale": "guess"}
    )
    out = await ph.request_submit(proposal, ctx)
    assert out["result_ref"] == "REQ-NEW"
    assert ctx.calls[0]["project_id"] == "proj-7" and ctx.calls[0]["description"] == "build X"


async def test_request_submit_unassigned_when_no_project():
    ctx = _SubmitCtx()
    proposal = _confirmed("request.submit", payload={"description": "build X"})
    await ph.request_submit(proposal, ctx)
    # None project_id → orchestrator.submit defaults it to Unassigned (HAI-34)
    assert ctx.calls[0]["project_id"] is None


async def test_request_submit_requires_description():
    ctx = _SubmitCtx()
    out = await run_confirmed_proposal(_confirmed("request.submit", payload={}), _reg(), ctx)
    assert out["status"] == "failed" and "description" in out["error"]
