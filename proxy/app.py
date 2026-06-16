"""OpenAI-compatible proxy in front of Claude Platform on AWS (Hermes brain).

Hermes (``provider: custom``, ``api_mode: chat_completions``) speaks the OpenAI
Chat Completions wire format. This service translates that to the Anthropic
Messages API via ``AsyncAnthropicAWS`` (Claude Platform on AWS) and back — so
Hermes can run on cloud Claude with reliable tool-calling and **no local model**
(keeping the Mac fast). It reuses the same AWS credentials the agent team uses;
no OpenAI account is involved — "OpenAI-compatible" refers only to the JSON shape.

Endpoints:
  GET  /healthz               liveness + creds/model visibility
  GET  /v1/models             advertises the configured model
  POST /v1/chat/completions   the translating endpoint (stream + non-stream)

The translation functions (``to_anthropic_request`` / ``to_openai_response`` /
``sse_chunks``) are pure and unit-tested without the SDK.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ── Config (env) ─────────────────────────────────────────────────────────────
# claude-opus-4-8 is what the agent team runs on and is confirmed provisioned on
# the AWS workspace. Override via PROXY_DEFAULT_MODEL (e.g. a Sonnet id) if your
# workspace has a cheaper model provisioned.
DEFAULT_MODEL = os.getenv("PROXY_DEFAULT_MODEL", "claude-opus-4-8")
INFERENCE_GEO = (os.getenv("ANTHROPIC_AWS_INFERENCE_GEO", "us") or "us").strip().lower()
DEFAULT_MAX_TOKENS = int(os.getenv("PROXY_DEFAULT_MAX_TOKENS", "4096"))
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "").strip()  # optional bearer to guard the proxy

app = FastAPI(title="hermes-llm-proxy")

_client: Any = None


def _get_client() -> Any:
    """Lazily build the AsyncAnthropicAWS client (mirrors src/agents/executor.py:
    api_key + workspace_id + aws_region). inference_geo is NOT a constructor arg —
    it's passed per-call to messages.create()."""
    global _client
    if _client is None:
        from anthropic import AsyncAnthropicAWS  # type: ignore[attr-defined]

        api_key = os.getenv("ANTHROPIC_AWS_API_KEY", "").strip()
        workspace_id = os.getenv("ANTHROPIC_AWS_WORKSPACE_ID", "").strip()
        aws_region = (os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1")
        if not api_key or not workspace_id:
            raise RuntimeError(
                "ANTHROPIC_AWS_API_KEY / ANTHROPIC_AWS_WORKSPACE_ID not set"
            )
        _client = AsyncAnthropicAWS(
            api_key=api_key,
            workspace_id=workspace_id,
            aws_region=aws_region,
        )
    return _client


# ── Translation helpers (pure) ───────────────────────────────────────────────

def _content_to_text(content: Any) -> str:
    """Flatten an OpenAI message ``content`` (str, or list of parts) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text" or "text" in p:
                    parts.append(p.get("text", ""))
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return str(content)


def _map_model(name: str | None) -> str:
    """Pass through a real claude-* id; otherwise use the configured default
    (so Hermes can use a friendly alias like 'agent-team-router')."""
    if name and name.startswith("claude-"):
        return name
    return DEFAULT_MODEL


def _map_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function", {})
        out.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", "") or "",
                # OpenAI 'parameters' (JSON Schema) == Anthropic 'input_schema'
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out or None


def _map_tool_choice(tc: Any) -> dict | None:
    if tc is None:
        return None
    if tc == "auto":
        return {"type": "auto"}
    if tc == "required":
        return {"type": "any"}
    if tc == "none":
        return None  # Anthropic has no explicit 'none'; omit → model may still answer in text
    if isinstance(tc, dict) and tc.get("type") == "function":
        name = (tc.get("function") or {}).get("name")
        if name:
            return {"type": "tool", "name": name}
    return {"type": "auto"}


def to_anthropic_request(body: dict) -> dict:
    """OpenAI chat-completions body → kwargs for ``messages.create`` (no client)."""
    system_parts: list[str] = []
    a_messages: list[dict] = []

    for m in body.get("messages") or []:
        role = m.get("role")
        if role == "system":
            text = _content_to_text(m.get("content"))
            if text:
                system_parts.append(text)
        elif role == "tool":
            a_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id"),
                            "content": _content_to_text(m.get("content")),
                        }
                    ],
                }
            )
        elif role == "assistant":
            blocks: list[dict] = []
            text = _content_to_text(m.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        parsed = {}
                else:
                    parsed = raw_args or {}
                blocks.append(
                    {"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"), "input": parsed}
                )
            if blocks:  # Anthropic rejects empty assistant content
                a_messages.append({"role": "assistant", "content": blocks})
        else:  # user (and any unknown role) → plain user text
            a_messages.append({"role": "user", "content": _content_to_text(m.get("content"))})

    kwargs: dict[str, Any] = {
        "model": _map_model(body.get("model")),
        "messages": a_messages,
        # Anthropic REQUIRES max_tokens — always set it.
        "max_tokens": int(body.get("max_tokens") or DEFAULT_MAX_TOKENS),
    }
    system = "\n\n".join(system_parts)
    if system:
        kwargs["system"] = system
    if body.get("temperature") is not None:
        kwargs["temperature"] = body["temperature"]
    tools = _map_tools(body.get("tools"))
    if tools:
        kwargs["tools"] = tools
    choice = _map_tool_choice(body.get("tool_choice"))
    if choice is not None:
        kwargs["tool_choice"] = choice
    return kwargs


def to_openai_response(resp: dict, model: str) -> dict:
    """Anthropic Messages response (as dict) → OpenAI chat.completion."""
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for b in resp.get("content") or []:
        btype = b.get("type")
        if btype == "text":
            text_parts.append(b.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": b.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": b.get("name"),
                        "arguments": json.dumps(b.get("input") or {}),
                    },
                }
            )

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    stop = resp.get("stop_reason")
    finish = "tool_calls" if tool_calls else ("length" if stop == "max_tokens" else "stop")

    usage_src = resp.get("usage") or {}
    pt = usage_src.get("input_tokens", 0) or 0
    ct = usage_src.get("output_tokens", 0) or 0

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }


def sse_chunks(oai: dict):
    """Emit a completed OpenAI response as a single chat.completion.chunk SSE
    event + [DONE]. (v1 streaming shim — satisfies streaming clients without
    token-by-token deltas.)"""
    choice = oai["choices"][0]
    delta = dict(choice["message"])
    # OpenAI streaming tool_calls need an 'index' per call.
    if delta.get("tool_calls"):
        delta["tool_calls"] = [
            {**tc, "index": i} for i, tc in enumerate(delta["tool_calls"])
        ]
    chunk = {
        "id": oai["id"],
        "object": "chat.completion.chunk",
        "created": oai["created"],
        "model": oai["model"],
        "choices": [{"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}],
    }
    yield f"data: {json.dumps(chunk)}\n\n"
    yield "data: [DONE]\n\n"


# ── Auth ─────────────────────────────────────────────────────────────────────

def _check_auth(request: Request) -> None:
    if not PROXY_API_KEY:
        return
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token != PROXY_API_KEY:
        raise HTTPException(status_code=401, detail="invalid proxy api key")


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz() -> dict:
    creds = bool(
        os.getenv("ANTHROPIC_AWS_API_KEY") and os.getenv("ANTHROPIC_AWS_WORKSPACE_ID")
    )
    return {
        "status": "ok" if creds else "degraded",
        "model": DEFAULT_MODEL,
        "inference_geo": INFERENCE_GEO,
        "creds_present": creds,
    }


@app.get("/v1/models")
async def list_models() -> dict:
    return {
        "object": "list",
        "data": [{"id": DEFAULT_MODEL, "object": "model", "owned_by": "anthropic-aws"}],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    _check_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    kwargs = to_anthropic_request(body)
    # inference_geo is a PER-CALL param (rejected on models < 4.6; our router model
    # is Sonnet 4.7, so it's safe). Matches src/agents/base.py.
    if INFERENCE_GEO:
        kwargs["inference_geo"] = INFERENCE_GEO
    try:
        client = _get_client()
        resp = await client.messages.create(**kwargs)
    except RuntimeError as e:  # missing creds
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001 — surface upstream errors as 502
        raise HTTPException(status_code=502, detail=f"upstream error: {e}")

    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    oai = to_openai_response(data, body.get("model") or DEFAULT_MODEL)

    if body.get("stream"):
        return StreamingResponse(sse_chunks(oai), media_type="text/event-stream")
    return JSONResponse(oai)
