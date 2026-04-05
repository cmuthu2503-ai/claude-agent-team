"""P8-T14: Cost tracking tests — token usage, pricing, budget enforcement."""

import pytest
from src.core.token_tracker import TokenTracker
from src.core.budget import BudgetEnforcer, BudgetExceededError
from src.models.base import TokenUsage
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def cost_setup(tmp_path):
    state = SQLiteStateStore(db_path=str(tmp_path / "cost.db"))
    await state.initialize()
    yield state
    await state.close()


# ── Token Tracker Tests ──────────────────────────

def test_cost_calculation_opus():
    tracker = TokenTracker.__new__(TokenTracker)
    tracker._pricing = {
        "claude-opus-4-6": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    }
    # 1000 input + 2000 output tokens with Opus pricing
    cost = tracker.calculate_cost("claude-opus-4-6", 1000, 2000)
    expected = (1000 * 15.00 + 2000 * 75.00) / 1_000_000
    assert abs(cost - expected) < 0.0001


def test_cost_calculation_sonnet():
    tracker = TokenTracker.__new__(TokenTracker)
    tracker._pricing = {
        "claude-opus-4-6": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    }
    cost = tracker.calculate_cost("claude-sonnet-4-6", 5000, 10000)
    expected = (5000 * 3.00 + 10000 * 15.00) / 1_000_000
    assert abs(cost - expected) < 0.0001


def test_cost_calculation_unknown_model():
    tracker = TokenTracker.__new__(TokenTracker)
    tracker._pricing = {}
    cost = tracker.calculate_cost("unknown-model", 1000, 2000)
    assert cost == 0.0


async def test_record_token_usage(cost_setup):
    state = cost_setup
    tracker = TokenTracker(state)
    usage = await tracker.record(
        request_id="REQ-001", subtask_id="REQ-001-BE",
        agent_id="backend_specialist", model="claude-sonnet-4-6",
        input_tokens=2000, output_tokens=5000,
    )
    assert usage.cost_usd > 0
    records = await state.get_token_usage_for_request("REQ-001")
    assert len(records) == 1
    assert records[0].agent_id == "backend_specialist"


async def test_daily_cost_aggregation(cost_setup):
    state = cost_setup
    tracker = TokenTracker(state)
    await tracker.record("REQ-A", "REQ-A-1", "be", "claude-sonnet-4-6", 1000, 1000)
    await tracker.record("REQ-B", "REQ-B-1", "fe", "claude-sonnet-4-6", 2000, 2000)
    daily = await state.get_daily_cost()
    assert daily > 0


# ── Budget Enforcer Tests ────────────────────────

async def test_budget_check_under_limit(cost_setup):
    state = cost_setup
    enforcer = BudgetEnforcer(state)
    result = await enforcer.check_budget()
    assert result["allowed"] is True
    assert result["warning"] is False


async def test_budget_enforcer_loads_config():
    enforcer = BudgetEnforcer.__new__(BudgetEnforcer)
    enforcer._config = {
        "daily_limit_usd": 50.0,
        "monthly_limit_usd": 500.0,
        "per_request_limit_usd": 10.0,
        "alert_threshold_pct": 0.8,
    }
    assert enforcer.daily_limit == 50.0
    assert enforcer.monthly_limit == 500.0
    assert enforcer.per_request_limit == 10.0
    assert enforcer.alert_threshold == 0.8


async def test_budget_exceeded_raises(cost_setup):
    state = cost_setup
    # Record enough usage to exceed a tiny limit
    tracker = TokenTracker(state)
    # Record a large cost
    usage = TokenUsage(
        usage_id="tu-big", request_id="REQ-X", subtask_id="REQ-X-1",
        agent_id="be", model="opus", input_tokens=0, output_tokens=0,
        cost_usd=100.0,
    )
    await state.record_token_usage(usage)

    enforcer = BudgetEnforcer.__new__(BudgetEnforcer)
    enforcer.state = state
    enforcer._config = {
        "daily_limit_usd": 50.0,
        "monthly_limit_usd": 500.0,
        "alert_threshold_pct": 0.8,
    }
    with pytest.raises(BudgetExceededError):
        await enforcer.check_budget()
