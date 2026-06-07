"""Tests for the agent-mode startup guard (resolve_agent_mode).

Mock mode (fake agent output) is OPT-IN: missing LLM credentials hard-fail by
default and are forbidden outright in staging/production, so a misconfigured
deploy can never silently serve simulated results.
"""

import pytest

from src.main import MockModeNotAllowedError, resolve_agent_mode


def test_real_client_always_real_llm():
    # A live client wins regardless of env / allow_mock.
    assert resolve_agent_mode(has_client=True, environment="development", allow_mock=False) == "real_llm"
    assert resolve_agent_mode(has_client=True, environment="production", allow_mock=False) == "real_llm"


@pytest.mark.parametrize("env", ["staging", "production"])
def test_no_client_forbidden_env_raises(env):
    # staging/production NEVER run fake agents, even if allow_mock is set.
    with pytest.raises(MockModeNotAllowedError):
        resolve_agent_mode(has_client=False, environment=env, allow_mock=True)


def test_no_client_default_deny_raises():
    # development without an explicit opt-in must NOT silently run mock.
    with pytest.raises(MockModeNotAllowedError):
        resolve_agent_mode(has_client=False, environment="development", allow_mock=False)


@pytest.mark.parametrize("env", ["development", "demo"])
def test_no_client_opt_in_allows_mock(env):
    assert resolve_agent_mode(has_client=False, environment=env, allow_mock=True) == "mock"


def test_error_message_points_to_setup_doc():
    with pytest.raises(MockModeNotAllowedError) as exc:
        resolve_agent_mode(has_client=False, environment="production", allow_mock=False)
    assert "setup-claude-platform-on-aws" in str(exc.value)
