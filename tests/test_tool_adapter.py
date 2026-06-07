"""PAM-04 / PAM-08 — PromptedToolAdapter parser tests.

Adversarial cases per PAM-08 spec:
  - Well-formed single call
  - Multiple calls per turn
  - Markdown ```xml fences around the whole response
  - Markdown ```json fence inside <tool_call> instead of <arguments>
  - Whitespace noise + uppercase tag variants
  - Missing <tool_name> → skipped + warning
  - Empty <tool_name> → skipped + warning
  - Malformed JSON arguments → call survives with args_parse_error
  - JSON with bare newlines in strings → auto-repaired
  - JSON that's a list (not a dict) → marked as error
  - Zero calls (model gave a final answer) → final_answer_text carries it
  - Empty input → empty result, no crash
"""

from __future__ import annotations

import json

import pytest

from src.agents.tool_adapter import (
    ParsedToolCall,
    ParseResult,
    PromptedToolAdapter,
    _attempt_json_repair,
)


@pytest.fixture
def adapter() -> PromptedToolAdapter:
    return PromptedToolAdapter()


# ── inject_instructions ──────────────────────────────────────────────────


def test_inject_instructions_appends_when_tools_present(adapter):
    sys = "You are a helpful assistant."
    tools = [{
        "name": "policy_check",
        "description": "Run quality rules over emissions.",
        "input_schema": {"type": "object", "properties": {"emissions": {"type": "array"}}},
    }]
    out = adapter.inject_instructions(sys, tools)
    assert "You are a helpful assistant" in out
    assert "## Tool Use" in out
    assert "<tool_call>" in out
    assert "policy_check" in out
    # The schema dict is JSON-dumped so the model can reference it
    assert '"emissions"' in out


def test_inject_instructions_no_change_when_no_tools(adapter):
    sys = "You are a helpful assistant."
    assert adapter.inject_instructions(sys, []) == sys


def test_inject_instructions_includes_per_tool_catalog(adapter):
    sys = ""
    tools = [
        {"name": "a", "description": "first", "input_schema": {}},
        {"name": "b", "description": "second", "input_schema": {}},
    ]
    out = adapter.inject_instructions(sys, tools)
    assert "`a`" in out and "`b`" in out
    assert "first" in out and "second" in out


# ── parse_tool_calls — happy paths ───────────────────────────────────────


def test_parse_single_well_formed_call(adapter):
    text = """
    I need to run the policy check first.

    <tool_call>
      <tool_name>policy_check</tool_name>
      <arguments>{"emissions": []}</arguments>
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    c = result.calls[0]
    assert c.tool_name == "policy_check"
    assert c.arguments == {"emissions": []}
    assert c.args_parse_error is None
    # Reasoning prose survives in final_answer_text
    assert "I need to run the policy check first" in result.final_answer_text


def test_parse_multiple_calls_per_turn(adapter):
    text = """
    <tool_call>
      <tool_name>first</tool_name>
      <arguments>{"x": 1}</arguments>
    </tool_call>
    <tool_call>
      <tool_name>second</tool_name>
      <arguments>{"y": 2}</arguments>
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert [c.tool_name for c in result.calls] == ["first", "second"]
    assert result.calls[0].arguments == {"x": 1}
    assert result.calls[1].arguments == {"y": 2}


def test_parse_handles_no_tool_calls_as_final_answer(adapter):
    """When the model decides it has enough info, it just writes prose."""
    text = "The policy check passed; no further action needed."
    result = adapter.parse_tool_calls(text)
    assert result.calls == []
    assert result.final_answer_text == text


def test_parse_handles_empty_input(adapter):
    result = adapter.parse_tool_calls("")
    assert result.calls == []
    assert result.final_answer_text == ""
    assert result.warnings == []


# ── parse_tool_calls — adversarial cases (PAM-08 spec) ──────────────────


def test_parse_strips_outer_markdown_fence(adapter):
    """Models often wrap their entire response in ```xml ... ``` despite
    the prompt telling them not to. The fence-stripper handles this."""
    text = """```xml
<tool_call>
  <tool_name>policy_check</tool_name>
  <arguments>{"emissions": []}</arguments>
</tool_call>
```"""
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].tool_name == "policy_check"


def test_parse_accepts_uppercase_tag_variants(adapter):
    """<TOOL_CALL> / <Tool_Name> / mixed case all parse — models vary."""
    text = """
    <TOOL_CALL>
      <Tool_Name>my_tool</Tool_Name>
      <ARGUMENTS>{"a": 1}</ARGUMENTS>
    </TOOL_CALL>
    """
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].tool_name == "my_tool"
    assert result.calls[0].arguments == {"a": 1}


def test_parse_handles_excess_whitespace(adapter):
    text = """
       <tool_call>


       <tool_name>   my_tool   </tool_name>


       <arguments>


       {"a": 1}


       </arguments>


       </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].tool_name == "my_tool"
    assert result.calls[0].arguments == {"a": 1}


def test_parse_markdown_fence_fallback_for_arguments(adapter):
    """Some models put arguments in a ```json fence INSIDE the tool_call
    block instead of inside <arguments> tags. We accept this with a
    soft warning."""
    text = """
    <tool_call>
      <tool_name>my_tool</tool_name>
      ```json
      {"a": 1, "b": [2, 3]}
      ```
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].tool_name == "my_tool"
    assert result.calls[0].arguments == {"a": 1, "b": [2, 3]}
    # Soft warning surfaced for agent trace visibility
    assert any("fence" in w.lower() for w in result.warnings)


def test_parse_call_with_no_arguments_yields_empty_dict(adapter):
    """Some tools take zero args. A <tool_call> with just <tool_name>
    should parse as `arguments={}`, not skipped."""
    text = """
    <tool_call>
      <tool_name>ping</tool_name>
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].tool_name == "ping"
    assert result.calls[0].arguments == {}
    assert result.calls[0].args_parse_error is None


# ── parse_tool_calls — malformed input ───────────────────────────────────


def test_parse_skips_call_with_missing_tool_name(adapter):
    text = """
    <tool_call>
      <arguments>{"a": 1}</arguments>
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert result.calls == []
    assert any("tool_name" in w for w in result.warnings)


def test_parse_skips_call_with_empty_tool_name(adapter):
    text = """
    <tool_call>
      <tool_name>  </tool_name>
      <arguments>{}</arguments>
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert result.calls == []
    assert result.warnings


def test_parse_call_with_invalid_json_args_surfaces_error(adapter):
    """Malformed JSON in <arguments> doesn't drop the call — the
    caller sees a ParsedToolCall with args_parse_error set so it can
    decide whether to retry the model or return an error."""
    text = """
    <tool_call>
      <tool_name>my_tool</tool_name>
      <arguments>{this is not json}</arguments>
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].tool_name == "my_tool"
    assert result.calls[0].arguments == {}
    assert result.calls[0].args_parse_error is not None
    assert "json" in result.calls[0].args_parse_error.lower() or \
        "expecting" in result.calls[0].args_parse_error.lower()


def test_parse_auto_repairs_bare_newlines_in_string_values(adapter):
    """Small open models often emit literal newlines inside quoted
    JSON strings. The repair pass escapes them and the call lands."""
    text = '<tool_call><tool_name>x</tool_name>' \
           '<arguments>{"msg": "line one\nline two"}</arguments></tool_call>'
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].arguments == {"msg": "line one\nline two"}
    assert any("auto-repaired" in w for w in result.warnings)


def test_parse_marks_non_dict_args_as_error(adapter):
    """Arguments MUST be a JSON object. A list, string, or number
    is recorded as args_parse_error."""
    text = """
    <tool_call>
      <tool_name>my_tool</tool_name>
      <arguments>["wrong", "shape"]</arguments>
    </tool_call>
    """
    result = adapter.parse_tool_calls(text)
    assert len(result.calls) == 1
    assert result.calls[0].args_parse_error
    assert "object" in result.calls[0].args_parse_error


# ── format_tool_result ───────────────────────────────────────────────────


def test_format_tool_result_dict_payload(adapter):
    call = ParsedToolCall(
        tool_name="policy_check",
        arguments={},
        raw_block="<tool_call>...</tool_call>",
    )
    out = adapter.format_tool_result(call, {"verdict": "PASS", "violations": []})
    assert "<tool_result>" in out
    assert "policy_check" in out
    assert '"verdict": "PASS"' in out  # JSON-formatted dict
    assert "<tool_error>" not in out


def test_format_tool_result_string_payload(adapter):
    call = ParsedToolCall(tool_name="x", arguments={}, raw_block="")
    out = adapter.format_tool_result(call, "plain text output")
    assert "plain text output" in out


def test_format_tool_result_error_flag_changes_tag(adapter):
    """is_error=True → <tool_error> instead of <tool_result>."""
    call = ParsedToolCall(tool_name="x", arguments={}, raw_block="")
    out = adapter.format_tool_result(call, "broken", is_error=True)
    assert "<tool_error>" in out
    assert "</tool_error>" in out
    assert "<tool_result>" not in out


def test_format_tool_result_preserves_unicode(adapter):
    """JSON serialisation must NOT escape non-ASCII — the prompt
    context handles UTF-8 natively and escaped sequences waste tokens."""
    call = ParsedToolCall(tool_name="x", arguments={}, raw_block="")
    out = adapter.format_tool_result(call, {"label": "café → result"})
    assert "café" in out
    assert "→" in out
    assert "\\u" not in out


# ── _attempt_json_repair unit ────────────────────────────────────────────


def test_repair_escapes_newlines_inside_strings_only():
    """Newlines OUTSIDE quoted strings stay untouched; ones INSIDE
    get escaped."""
    bad = '{"a": "line1\nline2", "b": 3}\n'
    fixed = _attempt_json_repair(bad)
    # The newline inside the string is escaped; the trailing one outside
    # is preserved.
    assert '"line1\\nline2"' in fixed
    assert fixed.endswith("\n")  # the structural newline survives
    parsed = json.loads(fixed)
    assert parsed == {"a": "line1\nline2", "b": 3}


def test_repair_handles_escaped_quote_inside_string():
    """An escaped quote inside a string MUST NOT terminate the string-
    detection state machine."""
    bad = '{"a": "she said \\"hi\\"\nand left"}'
    fixed = _attempt_json_repair(bad)
    parsed = json.loads(fixed)
    assert parsed == {"a": 'she said "hi"\nand left'}


def test_repair_no_op_on_clean_input():
    clean = '{"a": "no newlines", "b": [1, 2, 3]}'
    assert _attempt_json_repair(clean) == clean


# ── End-to-end: round-trip via parse + format ───────────────────────────


def test_parse_then_format_round_trip(adapter):
    """The output of format_tool_result inserted back into a model's
    response shouldn't be misparsed as another tool_call (tags are
    <tool_result>, not <tool_call>)."""
    text = """
    <tool_call>
      <tool_name>x</tool_name>
      <arguments>{"a": 1}</arguments>
    </tool_call>
    """
    r1 = adapter.parse_tool_calls(text)
    assert len(r1.calls) == 1
    formatted = adapter.format_tool_result(r1.calls[0], {"ok": True})
    # The formatted result must NOT contain a recognisable tool_call
    assert "<tool_call>" not in formatted
    # And feeding it back through parse extracts zero calls
    r2 = adapter.parse_tool_calls(formatted)
    assert r2.calls == []
