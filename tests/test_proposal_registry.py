"""HAI-24 (FR-037) — gated-action registry."""

from src.core.proposal_registry import (
    GATED_ACTION_TYPES,
    ProposalActionRegistry,
    parse_require_approval,
    requires_human_approval,
)


def test_gated_action_types_cover_the_lifecycle():
    for a in ("project.create", "prd.generate", "task.dispatch", "deploy", "rollback",
              "request.submit", "request.cancel", "agent.model.set"):
        assert ProposalActionRegistry.is_gated(a)
    # Reads are never gated.
    assert not ProposalActionRegistry.is_gated("monitor.list_requests")
    assert not ProposalActionRegistry.is_gated("")


async def _dummy(proposal, ctx):
    return {"result_ref": "x"}


def test_register_and_lookup():
    reg = ProposalActionRegistry()
    assert reg.get_handler("deploy") is None
    assert reg.has_handler("deploy") is False
    reg.register("deploy", _dummy)
    assert reg.get_handler("deploy") is _dummy
    assert reg.has_handler("deploy") is True
    assert reg.registered_action_types() == ["deploy"]


def test_register_ungated_warns_but_still_registers():
    reg = ProposalActionRegistry()
    reg.register("not.a.real.action", _dummy)   # logs a warning, doesn't raise
    assert reg.has_handler("not.a.real.action")


# ── HAI-64 — auto-approve policy ─────────────────────────────────────────────

def test_parse_require_approval_unset_is_none():
    # env unset → None → "gate everything" sentinel
    assert parse_require_approval(None) is None


def test_parse_require_approval_list_and_whitespace():
    assert parse_require_approval("deploy") == frozenset({"deploy"})
    assert parse_require_approval(" deploy , rollback ") == frozenset({"deploy", "rollback"})
    # present-but-empty → explicit empty set (fully autonomous), NOT None
    assert parse_require_approval("") == frozenset()


def test_requires_human_approval_default_gates_everything():
    # None policy (unset) → every action needs a human
    for a in ("project.create", "task.dispatch", "deploy", "rollback", "request.submit"):
        assert requires_human_approval(a, None) is True


def test_requires_human_approval_only_listed_when_set():
    policy = parse_require_approval("deploy")     # the user's "gate only deploy"
    assert requires_human_approval("deploy", policy) is True
    for a in ("project.create", "task.dispatch", "request.submit", "rollback"):
        assert requires_human_approval(a, policy) is False


def test_requires_human_approval_empty_is_fully_autonomous():
    policy = parse_require_approval("")            # empty set → nothing gated
    for a in ("deploy", "project.create", "task.dispatch"):
        assert requires_human_approval(a, policy) is False
