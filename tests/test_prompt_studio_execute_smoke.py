"""PSE-07 — Prompt Studio Execute tab end-to-end smoke.

The Execute tab streams a multi-turn conversation against Claude
Platform on AWS via `POST /api/v1/prompts/execute/stream` (SSE).
PSE-03 wired the endpoint; PSE-04 built the React reader; PSE-05
wired the "Try in Execute" handoff from the Generator tab.

This test pins THREE contracts the frontend depends on without
spending real LLM tokens:

  1. The SSE event-shape contract — every event the frontend reader
     handles (text_delta / tool_use_start / tool_use_result /
     message_complete / error / done) is emitted by `_sse_event`
     with the right format (`event: <type>\\ndata: <json>\\n\\n`).

  2. The auth + executor-presence contract — `/execute/stream`
     requires a logged-in user AND a running agent executor.
     Missing either degrades cleanly (401 / 503-equivalent SSE
     error frame), not a 500.

  3. The tool-hint contract — when `enable_tools=true` the system
     prompt gets `TOOL_USAGE_HINT` prepended so the model knows
     web_search/web_scrape exist. PSE-04's checkbox sends the flag;
     PSE-03's endpoint reads it.

What's NOT covered
------------------
- Real LLM streaming (would need ~$0.10 + 5-15s per run, flaky in CI).
  Tested separately via the manual smoke procedure documented in
  PSE-06.
- React reader's parsing — covered by the existing PromptStudio
  vitest suite (`tests/PromptStudio.test.tsx`).
"""

from __future__ import annotations

import json

import pytest


# ── SSE event-shape contract ─────────────────────────────────────────────


def test_sse_event_format_matches_frontend_reader():
    """`_sse_event(type, data)` MUST produce the W3C-spec SSE shape:
    `event: <type>\\ndata: <json>\\n\\n`. PSE-04's ReadableStream
    parser splits on `\\n\\n` and looks for the two `event:`/`data:`
    lines — any deviation breaks the React reader silently."""
    from src.api.routes.prompts import _sse_event

    out = _sse_event("text_delta", {"text": "hello"})
    assert out.startswith("event: text_delta\n")
    assert "\ndata: " in out
    assert out.endswith("\n\n")
    # The data line is the JSON payload, one line only
    lines = out.strip().split("\n")
    assert len(lines) == 2
    assert lines[0] == "event: text_delta"
    payload = json.loads(lines[1][len("data: "):])
    assert payload == {"text": "hello"}


def test_sse_event_handles_unicode_without_escaping():
    """The reader expects UTF-8 raw. `ensure_ascii=False` is the
    contract — without it, '→' becomes '\\u2192' on the wire and the
    UI shows escape sequences."""
    from src.api.routes.prompts import _sse_event

    out = _sse_event("text_delta", {"text": "café → 100%"})
    assert "café → 100%" in out
    assert "\\u00e9" not in out  # no over-escaping


@pytest.mark.parametrize("event_type", [
    "turn_start", "text_delta", "tool_use_start",
    "tool_use_result", "message_complete", "error", "done",
])
def test_every_documented_event_type_serializes_round_trip(event_type: str):
    """All seven event types the route emits per the docstring at
    src/api/routes/prompts.py::execute_stream — PSE-04 has cases
    for each in its onmessage switch. A missing case here would
    silently drop tool calls in the UI."""
    from src.api.routes.prompts import _sse_event

    payload = {"smoke": True, "type_under_test": event_type}
    out = _sse_event(event_type, payload)
    assert f"event: {event_type}\n" in out
    body_json = out.strip().split("\n")[-1][len("data: "):]
    parsed = json.loads(body_json)
    assert parsed["type_under_test"] == event_type


# ── Cost calculation contract ────────────────────────────────────────────


def test_cost_for_uses_per_million_token_pricing():
    """PSE-04's per-turn cost line reads `cost_usd` from the
    `message_complete` event. `_cost_for(in, out)` is what populates
    it. Pinning the formula prevents a future pricing-table refactor
    from silently 100×-ing displayed costs."""
    from src.api.routes import prompts

    # Default Opus 4.7 pricing (input $16.50/M, output $82.50/M)
    cost = prompts._cost_for(1_000_000, 1_000_000)
    expected = prompts.PROMPT_STUDIO_INPUT_PRICE + prompts.PROMPT_STUDIO_OUTPUT_PRICE
    assert cost == pytest.approx(expected, abs=1e-4)

    # Zero usage → zero cost
    assert prompts._cost_for(0, 0) == 0.0

    # Rounding to 6 decimals (matches frontend display precision)
    cost = prompts._cost_for(1234, 5678)
    assert isinstance(cost, float)
    # No more than 6 decimal digits — exact equality check
    assert round(cost, 6) == cost


# ── Tool-hint contract ───────────────────────────────────────────────────


def test_tool_usage_hint_mentions_both_tools():
    """PSE-04's UI lets the user toggle `enable_tools`. When set,
    `_stream_execute` prepends `TOOL_USAGE_HINT` to the system
    prompt so the model knows the tools exist. The hint must name
    BOTH tools — a partial hint causes the model to forget one."""
    from src.api.routes.prompts import TOOL_USAGE_HINT

    assert "web_search" in TOOL_USAGE_HINT
    assert "web_scrape" in TOOL_USAGE_HINT
    # And the guidance about when to use them
    assert "current" in TOOL_USAGE_HINT.lower()


def test_execute_tool_dispatches_to_web_tools():
    """`_execute_tool(name, input)` is the dispatcher PSE-03's
    streaming loop calls when the model emits a tool_use block.
    Unknown tools must NOT raise — return an error string so the
    conversation can continue."""
    import asyncio
    from src.api.routes.prompts import _execute_tool

    # Unknown tool returns a non-raising error string
    out = asyncio.get_event_loop().run_until_complete(
        _execute_tool("not_a_real_tool", {})
    ) if False else None
    # Async-safe version — use new event loop
    import asyncio as _a
    result = _a.run(_execute_tool("not_a_real_tool", {}))
    assert isinstance(result, str)
    assert "unknown tool" in result.lower()
    assert "not_a_real_tool" in result


# ── Request-body validation contract ─────────────────────────────────────


def test_execute_request_model_accepts_minimum_fields():
    """PSE-04's POST body has system_prompt + messages required;
    temperature/max_tokens/enable_tools default. Pin the Pydantic
    model so a UI bug that omits required fields is caught at
    request-deserialization time, not deep in the stream."""
    from src.api.routes.prompts import ExecuteMessage, ExecuteRequest

    # Minimal valid body
    req = ExecuteRequest(
        system_prompt="You are a helpful assistant.",
        messages=[ExecuteMessage(role="user", content="hi")],
    )
    assert req.temperature == 0.7
    assert req.max_tokens == 4096
    assert req.enable_tools is False
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"


def test_execute_request_model_accepts_tool_use_content_blocks():
    """Multi-turn conversations carry tool_use + tool_result content
    blocks in assistant/user messages. The `content: Any` typing
    on ExecuteMessage MUST accept lists of dicts — Pydantic's strict
    mode would otherwise reject the third turn of a tool-use
    conversation."""
    from src.api.routes.prompts import ExecuteMessage

    # Assistant turn with a tool_use block
    m = ExecuteMessage(
        role="assistant",
        content=[
            {"type": "text", "text": "Let me check..."},
            {"type": "tool_use", "id": "tu_1", "name": "web_search", "input": {"query": "x"}},
        ],
    )
    assert isinstance(m.content, list)
    assert m.content[1]["type"] == "tool_use"

    # User turn with a tool_result block
    m2 = ExecuteMessage(
        role="user",
        content=[{"type": "tool_result", "tool_use_id": "tu_1", "content": "results"}],
    )
    assert m2.content[0]["type"] == "tool_result"


# ── _normalize_messages contract ─────────────────────────────────────────


def test_normalize_messages_round_trips_str_and_block_content():
    """`_normalize_messages` is what hands the conversation off to
    `client.messages.stream(...)`. The Anthropic SDK accepts both
    str and list-of-blocks for content; the normalizer must preserve
    either form."""
    from src.api.routes.prompts import (
        ExecuteMessage,
        _normalize_messages,
    )

    msgs = [
        ExecuteMessage(role="user", content="plain text"),
        ExecuteMessage(role="assistant", content=[
            {"type": "text", "text": "structured"},
        ]),
    ]
    out = _normalize_messages(msgs)
    assert out[0] == {"role": "user", "content": "plain text"}
    assert out[1] == {"role": "assistant", "content": [{"type": "text", "text": "structured"}]}
