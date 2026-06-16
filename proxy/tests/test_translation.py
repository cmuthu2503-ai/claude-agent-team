"""HLP — OpenAI <-> Anthropic translation tests (no SDK / no network)."""

import json

from app import (
    sse_chunks,
    to_anthropic_request,
    to_openai_response,
)


# ── OpenAI → Anthropic (request) ─────────────────────────────────────────────

def test_system_message_is_hoisted_out_of_messages():
    body = {
        "model": "claude-sonnet-4-7",
        "messages": [
            {"role": "system", "content": "You are an operator."},
            {"role": "user", "content": "hi"},
        ],
    }
    out = to_anthropic_request(body)
    assert out["system"] == "You are an operator."
    assert out["messages"] == [{"role": "user", "content": "hi"}]   # system not in messages
    assert out["max_tokens"] >= 1                                    # always set (Anthropic requires it)


def test_multiple_system_messages_joined():
    body = {"messages": [
        {"role": "system", "content": "A"},
        {"role": "system", "content": "B"},
        {"role": "user", "content": "x"},
    ]}
    assert to_anthropic_request(body)["system"] == "A\n\nB"


def test_tools_parameters_become_input_schema():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    body = {
        "messages": [{"role": "user", "content": "make a project"}],
        "tools": [{"type": "function", "function": {
            "name": "create_project", "description": "Create a project", "parameters": schema,
        }}],
        "tool_choice": "auto",
    }
    out = to_anthropic_request(body)
    assert out["tools"] == [{
        "name": "create_project", "description": "Create a project", "input_schema": schema,
    }]
    assert out["tool_choice"] == {"type": "auto"}


def test_tool_choice_variants():
    def tc(v):
        return to_anthropic_request({"messages": [], "tool_choice": v}).get("tool_choice")
    assert tc("auto") == {"type": "auto"}
    assert tc("required") == {"type": "any"}
    assert tc({"type": "function", "function": {"name": "create_project"}}) == {
        "type": "tool", "name": "create_project"}
    # 'none' → omitted (no tool_choice key)
    assert "tool_choice" not in to_anthropic_request({"messages": [], "tool_choice": "none"})


def test_assistant_tool_calls_become_tool_use_blocks():
    body = {"messages": [
        {"role": "user", "content": "make X"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "create_project", "arguments": '{"name": "X"}'}},
        ]},
    ]}
    out = to_anthropic_request(body)
    asst = out["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "create_project", "input": {"name": "X"}},
    ]


def test_tool_role_becomes_tool_result_block():
    body = {"messages": [
        {"role": "tool", "tool_call_id": "call_1", "content": "ok, created proj-1"},
    ]}
    out = to_anthropic_request(body)
    assert out["messages"][0] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok, created proj-1"}],
    }


def test_max_tokens_default_and_override():
    assert to_anthropic_request({"messages": []})["max_tokens"] == 4096       # default
    assert to_anthropic_request({"messages": [], "max_tokens": 256})["max_tokens"] == 256


def test_unknown_model_falls_back_to_default():
    # default (env unset) is claude-opus-4-8 — confirmed provisioned on the workspace
    assert to_anthropic_request({"messages": [], "model": "agent-team-router"})["model"] == "claude-opus-4-8"
    assert to_anthropic_request({"messages": [], "model": "claude-sonnet-4-7"})["model"] == "claude-sonnet-4-7"


# ── Anthropic-strict fixes (found via live testing) ──────────────────────────

def test_temperature_is_dropped_by_default():
    # claude-opus-4-8 rejects `temperature` (deprecated). Don't forward it.
    out = to_anthropic_request({"messages": [{"role": "user", "content": "hi"}], "temperature": 0.7})
    assert "temperature" not in out


def test_tool_name_sanitized_and_round_tripped():
    name_map: dict = {}
    out = to_anthropic_request(
        {"messages": [], "tools": [{"type": "function", "function": {
            "name": "fs.read", "description": "d", "parameters": {"type": "object", "properties": {}}}}]},
        name_map,
    )
    assert out["tools"][0]["name"] == "fs_read"          # '.' → '_'
    assert name_map == {"fs_read": "fs.read"}            # sanitized → original recorded
    # response restores the original name the client expects
    resp = {"content": [{"type": "tool_use", "id": "t1", "name": "fs_read", "input": {}}],
            "stop_reason": "tool_use", "usage": {}}
    oai = to_openai_response(resp, "m", name_map)
    assert oai["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "fs.read"


def test_input_schema_gets_object_type_when_missing():
    # parameters present but without top-level "type" → coerced to object
    tool_no_type = {"type": "function", "function": {
        "name": "f", "parameters": {"properties": {"x": {"type": "string"}}}}}
    out = to_anthropic_request({"messages": [], "tools": [tool_no_type]})
    assert out["tools"][0]["input_schema"]["type"] == "object"
    assert out["tools"][0]["input_schema"]["properties"] == {"x": {"type": "string"}}

    # no parameters at all → minimal valid object schema
    tool_no_params = {"type": "function", "function": {"name": "g"}}
    out2 = to_anthropic_request({"messages": [], "tools": [tool_no_params]})
    assert out2["tools"][0]["input_schema"] == {"type": "object", "properties": {}}


# ── Anthropic → OpenAI (response) ────────────────────────────────────────────

def test_text_response_maps_to_content():
    resp = {
        "content": [{"type": "text", "text": "Hello there"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }
    out = to_openai_response(resp, "claude-sonnet-4-7")
    choice = out["choices"][0]
    assert choice["message"]["content"] == "Hello there"
    assert choice["message"].get("tool_calls") is None
    assert choice["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}


def test_tool_use_response_maps_to_tool_calls():
    resp = {
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "create_project", "input": {"name": "X"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 20, "output_tokens": 8},
    }
    out = to_openai_response(resp, "claude-sonnet-4-7")
    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    tc = choice["message"]["tool_calls"][0]
    assert tc["id"] == "toolu_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "create_project"
    assert json.loads(tc["function"]["arguments"]) == {"name": "X"}   # input → JSON string
    assert choice["message"]["content"] is None


def test_max_tokens_stop_reason_maps_to_length():
    resp = {"content": [{"type": "text", "text": "..."}], "stop_reason": "max_tokens", "usage": {}}
    assert to_openai_response(resp, "m")["choices"][0]["finish_reason"] == "length"


# ── Streaming shim ───────────────────────────────────────────────────────────

def test_sse_chunks_single_event_plus_done():
    oai = to_openai_response(
        {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn", "usage": {}},
        "m",
    )
    events = list(sse_chunks(oai))
    assert events[-1] == "data: [DONE]\n\n"
    assert events[0].startswith("data: ")
    chunk = json.loads(events[0][len("data: "):].strip())
    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["choices"][0]["delta"]["content"] == "hi"
    assert chunk["choices"][0]["finish_reason"] == "stop"


def test_sse_chunks_tool_calls_get_index():
    oai = to_openai_response(
        {"content": [{"type": "tool_use", "id": "t1", "name": "f", "input": {}}],
         "stop_reason": "tool_use", "usage": {}},
        "m",
    )
    chunk = json.loads(list(sse_chunks(oai))[0][len("data: "):].strip())
    assert chunk["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
