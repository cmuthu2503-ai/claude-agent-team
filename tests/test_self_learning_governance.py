"""HAI-48 (FR-062) — self-learning stays automatic; approval is a separate flag.

Two assertions encode the FR-062 contract:
  1. The self-learning LOOP runs automatically on a failure, regardless of governed
     mode — make_self_learning_handler takes no governance coupling, and a
     request.failed always kicks off the analysis.
  2. Whether the produced lesson REQUIRES APPROVAL is the independent
     LESSONS_REVIEW_GATE flag (AET-13), which exists and defaults to review-on.
"""

import asyncio
import inspect

from src.core.events import EventEmitter
from src.core.self_learning_trigger import make_self_learning_handler


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    async def single_agent_call(self, *, agent_id, prompt, label=None):
        self.calls.append({"agent_id": agent_id, "label": label})
        return {"text": "No new lesson needed."}


class _FakeState:
    async def get_request(self, rid):
        return None

    async def get_subtasks_for_request(self, rid):
        return []


async def _drain():
    for _ in range(40):
        await asyncio.sleep(0.01)


def test_handler_has_no_governance_parameter():
    # FR-062: the loop is automatic — it must NOT take a governed/approval gate.
    params = set(inspect.signature(make_self_learning_handler).parameters)
    assert "governed" not in params
    assert params == {"state", "agent_executor", "events"}


async def test_self_learning_fires_automatically_on_failure():
    events = EventEmitter()
    ex = _FakeExecutor()
    events.on(make_self_learning_handler(_FakeState(), ex, events))

    await events.emit("request.failed", {"request_id": "REQ-1", "error": "boom"})
    await _drain()

    # ran automatically — no human confirm, no governance check
    assert len(ex.calls) == 1
    assert ex.calls[0]["agent_id"] == "self_learning_agent"


async def test_unrelated_events_do_not_trigger():
    events = EventEmitter()
    ex = _FakeExecutor()
    events.on(make_self_learning_handler(_FakeState(), ex, events))
    await events.emit("request.completed", {"request_id": "REQ-1"})
    await _drain()
    assert ex.calls == []


def test_review_gate_flag_exists_and_defaults_review_on():
    # The "flag to require approval" half of FR-062 — independent of governed mode.
    import src.tools.lessons_writer as lw

    assert hasattr(lw, "REVIEW_GATE_ENABLED")
    # default (no env override in the test image) is review-on (require approval)
    assert lw.REVIEW_GATE_ENABLED is True
