"""Prompted-tool adapter (ReAct style) — PAM-04.

Models with ``tool_calling_mode='prompted'`` (Ollama local models, some
older third-party endpoints) don't speak the structured tool-use
protocol Anthropic / OpenAI native function-calling clients use.
``PromptedToolAdapter`` bridges the gap:

  1. ``inject_instructions(system_prompt, tools)`` — appends a section
      to the system prompt teaching the model the XML tool-call shape
      and listing every tool's name, description, and JSON schema.

  2. ``parse_tool_calls(text)`` — pulls structured tool calls out of
      the model's free-form text response. Tolerant: handles raw XML,
      XML wrapped in ```` ```xml ```` fences, multiple calls per turn,
      whitespace noise, and malformed-but-recoverable input.

  3. ``format_tool_result(call, result_text)`` — formats a tool's
      output for inclusion in the next-turn user message, in a shape
      the prompted model can recognise as "this is the answer to my
      tool call."

Why XML, not JSON
-----------------
Three reasons:
  - Tolerant parsers for XML-ish input are forgiving — a missing
    closing tag, an unescaped quote inside arguments, or a stray
    newline inside a tag are recoverable. Strict JSON dies on any of
    those.
  - Small open models (Gemma, Llama 3.x at the 7-12B size) are
    consistently better at producing well-formed XML than well-formed
    JSON in my benchmarking — JSON's escaping rules trip them up.
  - The output is human-readable in agent traces, which matters for
    the post-mortem flow when an agent went sideways.

Wire format
-----------
The adapter teaches the model to emit:

    <tool_call>
      <tool_name>policy_check</tool_name>
      <arguments>{"emissions": [...]}</arguments>
    </tool_call>

The ``<arguments>`` body is JSON, parsed with ``json.loads`` after the
outer tags are extracted. JSON inside XML inherits XML's tolerance for
surrounding whitespace while keeping the structured-arg semantics tools
need.

The model can emit multiple <tool_call> blocks per turn and they're
all extracted. A turn with no tool calls is also valid — that's the
final answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ── Public data types ────────────────────────────────────────────────────


@dataclass
class ParsedToolCall:
    """One tool invocation parsed from the model's text response.

    ``raw_block`` keeps the source XML so callers can dump it in error
    messages when the arguments don't validate. ``args_parse_error``
    is non-None when the JSON body couldn't be decoded — the caller
    can choose to retry the model or return an error to the user."""

    tool_name: str
    arguments: dict[str, Any]
    raw_block: str
    args_parse_error: str | None = None


@dataclass
class ParseResult:
    """Outcome of ``parse_tool_calls``. ``calls`` is the list of
    extracted invocations (possibly empty when the model gave a final
    answer with no tool use). ``final_answer_text`` is the text
    OUTSIDE the <tool_call> blocks — when calls is empty this is the
    user-facing response; when calls is non-empty it's reasoning
    prose the agent system can log."""

    calls: list[ParsedToolCall] = field(default_factory=list)
    final_answer_text: str = ""
    # Soft warnings — parser noticed something fishy but recovered.
    # Examples: "extra <tool_call> opener at offset 482 with no matching
    # close". Surfaced in agent traces for debuggability.
    warnings: list[str] = field(default_factory=list)


# ── Regex catalog ────────────────────────────────────────────────────────


# Strip ```xml ... ``` (or bare ```...```) fences that wrap the ENTIRE
# response. Anchored to whole-string start/end (\A and \Z) so we don't
# accidentally eat an INNER fence that belongs to the argument-fence
# fallback path. Caller strips before tag extraction so a fully-wrapped
# response still parses; nested fences are left intact for the inner
# fallback regex to find.
_FENCE_RE = re.compile(
    r"\A\s*```(?:xml|json|text)?\s*\n|\n?\s*```\s*\Z",
    re.IGNORECASE,
)

# Main tool-call block — non-greedy, multiline, case-insensitive on tag
# names so models that emit <Tool_Call> or <TOOL_CALL> still parse.
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)

# Inner <tool_name>…</tool_name>
_TOOL_NAME_RE = re.compile(
    r"<tool_name>\s*(.*?)\s*</tool_name>",
    re.DOTALL | re.IGNORECASE,
)

# Inner <arguments>…</arguments>
_ARGUMENTS_RE = re.compile(
    r"<arguments>\s*(.*?)\s*</arguments>",
    re.DOTALL | re.IGNORECASE,
)

# Fallback: model wrote arguments as ```json {...} ``` instead of
# wrapping in <arguments>. Triggered when <arguments> is missing but
# the block has a fenced JSON object.
_ARG_FENCE_FALLBACK_RE = re.compile(
    r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```",
    re.DOTALL | re.IGNORECASE,
)


# ── Adapter ──────────────────────────────────────────────────────────────


class PromptedToolAdapter:
    """ReAct-style tool-use bridge for prompted-mode models. Stateless;
    safe to share across agents / requests."""

    # ── 1. System-prompt injection ───────────────────────────────────

    INSTRUCTION_HEADER = (
        "\n\n"
        "## Tool Use\n\n"
        "You have access to the tools listed below. To use a tool, emit "
        "a <tool_call> block ANYWHERE in your response. You may use up to "
        "5 tools per turn; emit one block per tool use. After each block "
        "you will receive the tool's result in the next turn before being "
        "asked to continue.\n\n"
        "**Exact format — emit this verbatim:**\n\n"
        "<tool_call>\n"
        "  <tool_name>NAME_OF_TOOL</tool_name>\n"
        "  <arguments>{\"key\": \"value\"}</arguments>\n"
        "</tool_call>\n\n"
        "Rules:\n"
        "- `<arguments>` body MUST be valid JSON matching the tool's "
        "input_schema below.\n"
        "- When you have all the information you need, write your final "
        "answer as normal text OUTSIDE any <tool_call> block.\n"
        "- Do NOT wrap the <tool_call> block in markdown fences — emit "
        "the raw XML.\n"
        "- Do NOT invent tools that aren't listed; the result will be an "
        "error.\n\n"
        "### Available Tools\n\n"
    )

    def inject_instructions(
        self,
        system_prompt: str,
        tool_schemas: list[dict[str, Any]],
    ) -> str:
        """Append tool-use instructions + a per-tool catalog to *system_prompt*.

        Each tool schema is the dict returned by a tool's ``schema()``
        method (shape: ``{name, description, input_schema}``). The
        ``input_schema`` is included as a JSON code-fenced block so the
        model can reference it when constructing arguments."""
        if not tool_schemas:
            # No tools → no instructions to add. Caller's system prompt
            # is returned unchanged so prompted-mode models don't see
            # confusing scaffolding for capabilities they don't have.
            return system_prompt

        parts = [system_prompt.rstrip(), self.INSTRUCTION_HEADER.rstrip()]
        for schema in tool_schemas:
            name = schema.get("name", "?")
            description = schema.get("description", "(no description)")
            input_schema = schema.get("input_schema", {})
            parts.append(f"\n#### `{name}`\n\n{description}\n")
            parts.append(
                "Input schema:\n```json\n"
                + json.dumps(input_schema, indent=2)
                + "\n```\n"
            )
        return "\n".join(parts) + "\n"

    # ── 2. Tool-call parsing ─────────────────────────────────────────

    def parse_tool_calls(self, text_response: str) -> ParseResult:
        """Extract any <tool_call> blocks from the model's response.

        Returns a ``ParseResult`` with:
          - ``calls`` — every successfully-recognised tool invocation
            (may have args_parse_error set per call when the JSON
            inside <arguments> didn't decode)
          - ``final_answer_text`` — everything OUTSIDE the tool-call
            blocks, stripped of fence wrappers and trailing whitespace
          - ``warnings`` — soft warnings for the agent trace

        Never raises. Malformed input degrades to either a parse_error
        on the individual call or a warning on the overall result."""
        result = ParseResult()
        if not text_response:
            return result

        # Strip outer fences models love wrapping their whole response in.
        cleaned = _FENCE_RE.sub("", text_response).strip()

        # Find every tool_call block. ``finditer`` so we know the
        # spans for stripping the final-answer text below.
        spans: list[tuple[int, int]] = []
        for m in _TOOL_CALL_RE.finditer(cleaned):
            spans.append((m.start(), m.end()))
            block_body = m.group(1)
            self._parse_one_block(block_body, m.group(0), result)

        # Final-answer text = original cleaned text with the tool_call
        # spans cut out. The result is the model's reasoning + final
        # response. Multiple consecutive whitespace collapsed to one
        # newline to keep traces readable.
        if spans:
            kept: list[str] = []
            cursor = 0
            for start, end in spans:
                kept.append(cleaned[cursor:start])
                cursor = end
            kept.append(cleaned[cursor:])
            final = "\n".join(p.strip() for p in kept if p.strip())
        else:
            final = cleaned

        result.final_answer_text = final.strip()
        return result

    def _parse_one_block(
        self,
        block_body: str,
        raw_block: str,
        result: ParseResult,
    ) -> None:
        """Parse the inside of a single <tool_call>…</tool_call> body.
        Appends one ``ParsedToolCall`` to ``result.calls`` (with
        ``args_parse_error`` set when the JSON didn't decode)."""
        name_match = _TOOL_NAME_RE.search(block_body)
        if not name_match:
            result.warnings.append(
                f"<tool_call> block has no <tool_name> child; skipped "
                f"(block[:100]={block_body[:100]!r})"
            )
            return
        tool_name = name_match.group(1).strip()
        if not tool_name:
            result.warnings.append("<tool_name> was empty; skipped")
            return

        # Try the standard <arguments>…</arguments> path first.
        args_text: str | None = None
        args_match = _ARGUMENTS_RE.search(block_body)
        if args_match:
            args_text = args_match.group(1).strip()
        else:
            # Fallback: model wrapped arguments in a fenced JSON block.
            # We tolerate it but warn so we can spot the pattern in
            # traces and tighten the prompt if it gets common.
            fence_match = _ARG_FENCE_FALLBACK_RE.search(block_body)
            if fence_match:
                args_text = fence_match.group(1).strip()
                result.warnings.append(
                    f"<tool_call name={tool_name!r}> used markdown-fence "
                    "fallback for arguments instead of <arguments> tags"
                )

        if args_text is None:
            # No arguments at all — some tools take zero args, so we
            # emit a call with an empty dict rather than skipping.
            result.calls.append(ParsedToolCall(
                tool_name=tool_name,
                arguments={},
                raw_block=raw_block,
                args_parse_error=None,
            ))
            return

        # Strip a wrapping fence inside the <arguments> body too,
        # for the case where the model wrote both forms.
        args_text = _FENCE_RE.sub("", args_text).strip()

        try:
            parsed = json.loads(args_text)
        except json.JSONDecodeError as e:
            # Soft attempt at recovery: some models forget to escape
            # newlines inside string fields. Try a permissive repair
            # by escaping bare newlines INSIDE quoted strings only.
            # If that still fails, surface the original error.
            try:
                repaired = _attempt_json_repair(args_text)
                parsed = json.loads(repaired)
                result.warnings.append(
                    f"<tool_call name={tool_name!r}> arguments JSON had "
                    "unescaped newlines inside strings; auto-repaired"
                )
            except Exception:  # noqa: BLE001
                result.calls.append(ParsedToolCall(
                    tool_name=tool_name,
                    arguments={},
                    raw_block=raw_block,
                    args_parse_error=str(e),
                ))
                return

        if not isinstance(parsed, dict):
            result.calls.append(ParsedToolCall(
                tool_name=tool_name,
                arguments={},
                raw_block=raw_block,
                args_parse_error=(
                    f"arguments must be a JSON object, got {type(parsed).__name__}"
                ),
            ))
            return

        result.calls.append(ParsedToolCall(
            tool_name=tool_name,
            arguments=parsed,
            raw_block=raw_block,
        ))

    # ── 3. Result formatting ─────────────────────────────────────────

    def format_tool_result(
        self,
        call: ParsedToolCall,
        result_payload: Any,
        is_error: bool = False,
    ) -> str:
        """Render a tool's output as the user-turn message body the
        prompted-mode model expects on the next iteration.

        Format:

            <tool_result>
              <tool_name>NAME</tool_name>
              <output>
                STRINGIFIED_OUTPUT
              </output>
            </tool_result>

        When ``result_payload`` is a dict / list, it's JSON-stringified
        with 2-space indent for readability in the prompt context. When
        it's already a string, it's used verbatim.

        ``is_error=True`` flips the tag to ``<tool_error>`` so the model
        can tell the difference between "tool ran but returned a low
        result" and "the call itself failed."""
        tag = "tool_error" if is_error else "tool_result"
        if isinstance(result_payload, (dict, list)):
            body = json.dumps(result_payload, indent=2, ensure_ascii=False)
        else:
            body = str(result_payload)
        return (
            f"<{tag}>\n"
            f"  <tool_name>{call.tool_name}</tool_name>\n"
            f"  <output>\n{body}\n  </output>\n"
            f"</{tag}>"
        )


# ── Soft JSON repair ─────────────────────────────────────────────────────


# Bare newlines INSIDE quoted JSON strings cause JSONDecodeError. Some
# small open models emit these regularly when stringifying multi-line
# content (commit messages, code snippets). The repair walks the text
# and escapes \n that occur between unescaped " marks.
def _attempt_json_repair(text: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            continue
        # Inside a string
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            out.append(ch)
            in_string = False
            continue
        if ch == "\n":
            out.append("\\n")
            continue
        if ch == "\r":
            out.append("\\r")
            continue
        if ch == "\t":
            out.append("\\t")
            continue
        out.append(ch)
    return "".join(out)
