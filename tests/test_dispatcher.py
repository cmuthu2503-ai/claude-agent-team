"""Tests for dispatcher and aggregator."""

import pytest

from src.config.loader import ConfigLoader
from src.core.aggregator import Aggregator, AggregationError
from src.core.dispatcher import DispatchError, Dispatcher


@pytest.fixture
def config():
    loader = ConfigLoader()
    loader.load_all()
    return loader


@pytest.fixture
def dispatcher(config):
    return Dispatcher(config)


@pytest.fixture
def aggregator():
    return Aggregator()


# ── Dispatcher Tests ─────────────────────────────


def test_valid_delegation(dispatcher):
    assert dispatcher.validate_delegation("engineering_lead", "prd_specialist") is True
    assert dispatcher.validate_delegation("engineering_lead", "code_reviewer") is True
    assert dispatcher.validate_delegation("engineering_lead", "devops_specialist") is True


def test_invalid_delegation(dispatcher):
    assert dispatcher.validate_delegation("engineering_lead", "backend_specialist") is False
    assert dispatcher.validate_delegation("backend_specialist", "prd_specialist") is False


def test_dispatch_valid(dispatcher):
    result = dispatcher.dispatch(
        "engineering_lead", "prd_specialist", {"description": "test"}
    )
    assert result["dispatched"] is True
    assert result["agent_id"] == "prd_specialist"


def test_dispatch_invalid_raises(dispatcher):
    with pytest.raises(DispatchError):
        dispatcher.dispatch("backend_specialist", "engineering_lead", {"description": "test"})


def test_get_delegation_targets(dispatcher):
    targets = dispatcher.get_delegation_targets("code_reviewer")
    assert "backend_specialist" in targets
    assert "frontend_specialist" in targets


def test_get_delegation_targets_leaf_agent(dispatcher):
    targets = dispatcher.get_delegation_targets("backend_specialist")
    assert targets == []


def test_route_by_domain(dispatcher):
    lead = dispatcher.route_by_domain(["backend"])
    assert lead == "code_reviewer"


def test_route_by_domain_deployment(dispatcher):
    lead = dispatcher.route_by_domain(["deployment"])
    assert lead == "devops_specialist"


def test_route_by_domain_fallback(dispatcher):
    lead = dispatcher.route_by_domain(["unknown_stuff"])
    assert lead == "engineering_lead"


# ── Aggregator Tests ─────────────────────────────


def test_aggregate_all_success(aggregator):
    results = [
        {"status": "completed", "artifacts": ["a.py"], "outputs": {"code": "ok"}},
        {"status": "completed", "artifacts": ["b.py"], "outputs": {"tests": "ok"}},
    ]
    combined = aggregator.aggregate(results)
    assert combined["status"] == "completed"
    assert len(combined["artifacts"]) == 2
    assert combined["succeeded"] == 2
    assert combined["failed"] == 0


def test_aggregate_partial_failure(aggregator):
    results = [
        {"status": "completed", "artifacts": ["a.py"], "outputs": {"code": "ok"}},
        {"status": "failed", "error": "timeout"},
    ]
    combined = aggregator.aggregate(results, allow_partial=True)
    assert combined["status"] == "partial"
    assert combined["succeeded"] == 1
    assert combined["failed"] == 1


def test_aggregate_all_failed_raises(aggregator):
    results = [
        {"status": "failed", "error": "err1"},
        {"status": "failed", "error": "err2"},
    ]
    with pytest.raises(AggregationError):
        aggregator.aggregate(results)


def test_aggregate_no_partial_raises(aggregator):
    results = [
        {"status": "completed", "artifacts": [], "outputs": {}},
        {"status": "failed", "error": "err"},
    ]
    with pytest.raises(AggregationError):
        aggregator.aggregate(results, allow_partial=False)


def test_build_summary(aggregator):
    aggregated = {
        "status": "completed",
        "artifacts": ["a.py", "b.py"],
        "errors": [],
    }
    summary = aggregator.build_summary(aggregated, "Build login page")
    assert "Build login page" in summary
    assert "completed" in summary
    assert "2" in summary
