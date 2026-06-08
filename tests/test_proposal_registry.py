"""HAI-24 (FR-037) — gated-action registry."""

from src.core.proposal_registry import GATED_ACTION_TYPES, ProposalActionRegistry


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
