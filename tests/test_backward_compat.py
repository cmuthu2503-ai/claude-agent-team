"""HAI-49 (NFR-001, M4) — no Hermes identity => pre-integration behavior.

The integration must be invisible to a deployment that never onboards Hermes: with
no service token, governance resolves OFF and NOTHING is gated — every autonomous
action executes inline exactly as before, and zero proposals are ever created.

Per-loop legacy behavior is covered in test_auto_dispatch_governed.py and
test_ops_heal_governed.py; this file is the consolidated gate-level guarantee.
"""

import pytest

from src.core.governance import resolve_governed_mode
from src.core.in_process_gate import submit_gated_action
from src.core.proposal_registry import GATED_ACTION_TYPES
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "bc.db"))
    await s.initialize()
    yield s
    await s.close()


async def test_no_service_token_resolves_legacy(store):
    # the default for a fresh, Hermes-free install
    assert await resolve_governed_mode(store, None) is False


@pytest.mark.parametrize("action_type", sorted(GATED_ACTION_TYPES))
async def test_legacy_never_proposes_any_gated_action(store, action_type):
    """With governance OFF, EVERY gated action executes inline and creates NO
    proposal — the gate is fully transparent (NFR-001)."""
    ran = {"v": False}

    async def execute():
        ran["v"] = True
        return "executed"

    out = await submit_gated_action(
        store, events=None, governed=False,
        action_type=action_type, proposed_by="system", execute=execute,
    )
    assert out.gated is False and ran["v"] is True
    assert out.result == "executed"
    assert await store.list_proposals() == []          # nothing queued, ever


async def test_governance_off_means_no_proposals_table_growth(store):
    """A burst of would-be-gated actions under legacy leaves the proposals table
    empty — a Hermes-free install accrues no approval backlog."""
    async def execute():
        return "ok"

    for at in ("deploy", "rollback", "task.dispatch", "project.create"):
        await submit_gated_action(
            store, events=None, governed=False, action_type=at,
            proposed_by="system", execute=execute,
        )
    assert len(await store.list_proposals()) == 0
