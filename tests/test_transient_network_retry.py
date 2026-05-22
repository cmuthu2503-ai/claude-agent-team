"""Regression tests for the transient-network retry classifier.

Pins the fix that closed REQ-E3A10E's failure class — a ~1-2 minute
network blip on the host cascaded through review + test + code_commit
because the retry loop in BaseAgent._call_anthropic didn't classify
the resulting errors as retryable.

The classifier is pattern-based (string contains) on purpose — the
Anthropic SDK wraps httpx errors at multiple layers and the exact
exception class isn't stable across SDK versions. Matching on the
human-readable message is the most resilient surface.
"""

from __future__ import annotations

from src.agents.base import _is_transient_network_error


# ── Real production errors observed in REQ-E3A10E ────────────────────────────


def test_classifies_anthropic_streaming_disconnect() -> None:
    """The Anthropic streaming response was cut mid-chunk."""
    assert _is_transient_network_error(
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)"
    )


def test_classifies_generic_connection_error() -> None:
    """anthropic.APIConnectionError stringifies as 'Connection error.'"""
    assert _is_transient_network_error("Connection error.")


def test_classifies_dns_failure() -> None:
    """GitHub publish raised this when /etc/resolv.conf became unreachable."""
    assert _is_transient_network_error(
        "GitHub HTTP error: [Errno -2] Name or service not known"
    )


# ── Other transient patterns worth catching ─────────────────────────────────


def test_classifies_connection_reset() -> None:
    assert _is_transient_network_error("Connection reset by peer")


def test_classifies_connection_refused() -> None:
    assert _is_transient_network_error("Connection refused")


def test_classifies_read_timeout() -> None:
    assert _is_transient_network_error("HTTPSConnectionPool: Read timed out")


def test_classifies_broken_pipe() -> None:
    assert _is_transient_network_error("BrokenPipeError: [Errno 32] Broken pipe")


# ── Anthropic API transient signals (added after REQ-FC2425) ─────────────────


def test_classifies_anthropic_overloaded_error_envelope() -> None:
    """The exact error envelope Anthropic returned in REQ-FC2425 cycle 0.
    HTTP 529 = transient throttle; retrying with backoff is the right move."""
    assert _is_transient_network_error(
        "{'type': 'error', 'error': {'details': None, "
        "'type': 'overloaded_error', 'message': 'Overloaded'}, "
        "'request_id': 'req_011CbJ9HhRHW5TS2xe4XCzQ1'}"
    )


def test_classifies_simple_overloaded_message() -> None:
    """The SDK sometimes surfaces this as a plain anthropic.APIError
    with message 'Overloaded'."""
    assert _is_transient_network_error("anthropic.APIError: Overloaded")


def test_classifies_503_service_unavailable() -> None:
    assert _is_transient_network_error("HTTP 503 Service Unavailable")


def test_classifies_500_internal_server_error() -> None:
    assert _is_transient_network_error(
        "anthropic.InternalServerError: 500 Internal Server Error"
    )


def test_case_insensitive() -> None:
    """Matching is case-insensitive so we don't have to maintain
    duplicate patterns for variant casings from different SDK layers."""
    assert _is_transient_network_error("CONNECTION ERROR.")
    assert _is_transient_network_error("Peer Closed Connection")


# ── Negative cases — NOT transient ──────────────────────────────────────────


def test_does_NOT_classify_validation_error() -> None:
    """An API validation rejection isn't transient — retrying won't help."""
    assert not _is_transient_network_error(
        "anthropic.BadRequestError: max_tokens exceeds context window"
    )


def test_does_NOT_classify_rate_limit() -> None:
    """Rate limit (429) has its OWN retry path with longer backoff —
    classifying it as a network error would double-retry it."""
    assert not _is_transient_network_error(
        "anthropic.RateLimitError: 429 too many requests"
    )


def test_does_NOT_classify_authentication_error() -> None:
    assert not _is_transient_network_error(
        "anthropic.AuthenticationError: 401 invalid API key"
    )


def test_does_NOT_classify_compile_errors() -> None:
    """Agent-emitted ruff errors are not transient — retrying without
    changing the code won't fix them."""
    assert not _is_transient_network_error(
        "Python compilation failed (ruff): F401 imported but unused"
    )


def test_empty_string_is_not_transient() -> None:
    assert not _is_transient_network_error("")
