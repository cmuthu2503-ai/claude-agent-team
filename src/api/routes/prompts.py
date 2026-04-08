"""Prompt Studio endpoints — generate, refine, select, history, templates, execute."""

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.agents.executor import VALID_PROVIDERS
from src.auth.service import get_current_user
from src.core.prompt_engineer import PromptEngineer, PromptEngineerError
from src.models.base import PromptSession, PromptVariant
from src.tools.firecrawl_tools import WebScrapeTool, WebSearchTool

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])

TEMPLATES_PATH = Path("config/prompt_templates.yaml")


def _validate_provider(provider: str) -> None:
    """Raise HTTPException 400 if the provider value is not recognized."""
    if provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid provider '{provider}'. Must be one of: "
                f"{sorted(VALID_PROVIDERS)}"
            ),
        )


def _envelope(data: Any, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta, "error": None}


def _get_engineer(request: Request) -> PromptEngineer:
    """Get a PromptEngineer instance using the existing AgentSystemExecutor's LLM clients."""
    executor = request.app.state.orchestrator._agent_executor
    if not executor:
        raise HTTPException(
            status_code=503,
            detail="Agent executor not initialized — LLM clients unavailable",
        )
    return PromptEngineer(
        anthropic_client=executor.anthropic_client,
        bedrock_client=executor.bedrock_client,
        openai_client=getattr(executor, "openai_client", None),
    )


# ── Request/Response Schemas ─────────────────────


class GenerateRequest(BaseModel):
    use_case: str
    target_audience: str = ""
    desired_output: str = ""
    tone: str = ""
    constraints: str = ""
    options: dict[str, Any] = {}
    provider: str = "anthropic_sonnet"
    template_id: str | None = None


class RefineRequest(BaseModel):
    feedback: str
    provider: str = "anthropic_sonnet"


class SelectRequest(BaseModel):
    variant_id: str


# ── Serialization helpers ────────────────────────


def _variant_to_dict(v: PromptVariant) -> dict[str, Any]:
    return {
        "variant_id": v.variant_id,
        "session_id": v.session_id,
        "iteration": v.iteration,
        "variant_index": v.variant_index,
        "approach": v.approach,
        "prompt_text": v.prompt_text,
        "techniques": v.techniques,
        "feedback_applied": v.feedback_applied,
        "generated_at": v.generated_at.isoformat(),
    }


def _session_to_dict(s: PromptSession, variants: list[PromptVariant] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "session_id": s.session_id,
        "user_id": s.user_id,
        "created_at": s.created_at.isoformat(),
        "use_case": s.use_case,
        "target_audience": s.target_audience,
        "desired_output": s.desired_output,
        "tone": s.tone,
        "constraints": s.constraints,
        "options": s.options,
        "provider": s.provider,
        "template_id": s.template_id,
        "selected_variant_id": s.selected_variant_id,
    }
    if variants is not None:
        d["variants"] = [_variant_to_dict(v) for v in variants]
    return d


# ── Endpoints ────────────────────────────────────


@router.get("/templates")
async def list_templates(user: dict = Depends(get_current_user)):
    """Return the list of starting templates from config/prompt_templates.yaml."""
    if not TEMPLATES_PATH.exists():
        return _envelope([])
    try:
        with open(TEMPLATES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("prompt_templates_load_failed", error=str(e))
        return _envelope([])

    templates = data.get("templates", {})
    result = []
    for template_id, cfg in templates.items():
        if not isinstance(cfg, dict):
            continue
        result.append({
            "template_id": template_id,
            "name": cfg.get("name", template_id),
            "description": cfg.get("description", ""),
            "category": cfg.get("category", ""),
            "use_case": cfg.get("use_case", ""),
            "target_audience": cfg.get("target_audience", ""),
            "desired_output": cfg.get("desired_output", ""),
            "tone": cfg.get("tone", ""),
            "constraints": cfg.get("constraints", ""),
        })
    return _envelope(result)


@router.post("/generate")
async def generate_prompt(
    body: GenerateRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Generate 3 variant prompts from structured inputs. Creates a new session."""
    if not body.use_case or not body.use_case.strip():
        raise HTTPException(status_code=400, detail="use_case is required")
    _validate_provider(body.provider)

    state = request.app.state.state_store
    engineer = _get_engineer(request)
    user_id = user.get("user_id") or user.get("username", "unknown")

    # Create the session
    session = PromptSession(
        session_id=f"PS-{uuid.uuid4().hex[:8].upper()}",
        user_id=user_id,
        use_case=body.use_case,
        target_audience=body.target_audience,
        desired_output=body.desired_output,
        tone=body.tone,
        constraints=body.constraints,
        options=body.options,
        provider=body.provider,
        template_id=body.template_id,
    )
    await state.create_prompt_session(session)

    # Generate variants via LLM
    try:
        variant_dicts = await engineer.generate_variants(
            use_case=body.use_case,
            target_audience=body.target_audience,
            desired_output=body.desired_output,
            tone=body.tone,
            constraints=body.constraints,
            options=body.options,
            provider=body.provider,
        )
    except PromptEngineerError as e:
        logger.error("prompt_generate_failed", session_id=session.session_id, error=str(e))
        raise HTTPException(status_code=502, detail=str(e))

    # Persist variants
    saved_variants: list[PromptVariant] = []
    for idx, vd in enumerate(variant_dicts, start=1):
        variant = PromptVariant(
            variant_id=f"{session.session_id}-V{idx:02d}-I0",
            session_id=session.session_id,
            iteration=0,
            variant_index=idx,
            approach=vd.get("approach", f"Variant {idx}"),
            prompt_text=vd.get("prompt", ""),
            techniques=vd.get("techniques", []),
        )
        await state.create_prompt_variant(variant)
        saved_variants.append(variant)

    logger.info(
        "prompt_session_created",
        session_id=session.session_id,
        variants=len(saved_variants),
        provider=body.provider,
    )

    return _envelope(_session_to_dict(session, saved_variants))


@router.post("/{session_id}/refine")
async def refine_prompt(
    session_id: str,
    body: RefineRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Refine the selected variant in a session using user feedback. Produces 3 new variants."""
    if not body.feedback or not body.feedback.strip():
        raise HTTPException(status_code=400, detail="feedback is required")

    state = request.app.state.state_store
    session = await state.get_prompt_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Prompt session not found")
    if not session.selected_variant_id:
        raise HTTPException(
            status_code=400,
            detail="No variant selected yet. Call PUT /:id/select first.",
        )

    # Fetch all variants and find the selected one
    all_variants = await state.get_prompt_variants_for_session(session_id)
    selected = next(
        (v for v in all_variants if v.variant_id == session.selected_variant_id),
        None,
    )
    if not selected:
        raise HTTPException(status_code=404, detail="Selected variant not found")

    # Determine next iteration number
    max_iter = max((v.iteration for v in all_variants), default=0)
    next_iteration = max_iter + 1

    # Run the refine
    engineer = _get_engineer(request)
    try:
        variant_dicts = await engineer.refine_variants(
            session_inputs={
                "use_case": session.use_case,
                "target_audience": session.target_audience,
                "desired_output": session.desired_output,
                "tone": session.tone,
                "constraints": session.constraints,
            },
            selected_prompt=selected.prompt_text,
            feedback=body.feedback,
            provider=body.provider,
        )
    except PromptEngineerError as e:
        logger.error("prompt_refine_failed", session_id=session_id, error=str(e))
        raise HTTPException(status_code=502, detail=str(e))

    # Persist new variants at the next iteration
    saved_variants: list[PromptVariant] = []
    for idx, vd in enumerate(variant_dicts, start=1):
        variant = PromptVariant(
            variant_id=f"{session_id}-V{idx:02d}-I{next_iteration}",
            session_id=session_id,
            iteration=next_iteration,
            variant_index=idx,
            approach=vd.get("approach", f"Variant {idx}"),
            prompt_text=vd.get("prompt", ""),
            techniques=vd.get("techniques", []),
            feedback_applied=body.feedback,
        )
        await state.create_prompt_variant(variant)
        saved_variants.append(variant)

    logger.info(
        "prompt_refined",
        session_id=session_id,
        iteration=next_iteration,
        variants=len(saved_variants),
    )

    # Return the full session with ALL variants (every iteration)
    all_variants = await state.get_prompt_variants_for_session(session_id)
    return _envelope(_session_to_dict(session, all_variants))


@router.put("/{session_id}/select")
async def select_variant(
    session_id: str,
    body: SelectRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Mark which variant the user selected. Required before refinement."""
    state = request.app.state.state_store
    session = await state.get_prompt_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Prompt session not found")

    # Validate the variant exists in this session
    variants = await state.get_prompt_variants_for_session(session_id)
    if not any(v.variant_id == body.variant_id for v in variants):
        raise HTTPException(status_code=404, detail="Variant not found in this session")

    await state.update_prompt_session_selection(session_id, body.variant_id)
    return _envelope({"session_id": session_id, "selected_variant_id": body.variant_id})


@router.get("")
async def list_sessions(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    user: dict = Depends(get_current_user),
):
    """List the current user's prompt sessions (history), most recent first."""
    state = request.app.state.state_store
    user_id = user.get("user_id") or user.get("username", "unknown")
    offset = (page - 1) * per_page
    sessions = await state.list_prompt_sessions_for_user(
        user_id=user_id, limit=per_page, offset=offset
    )
    # For history list we don't include full variant bodies — just session summaries
    return _envelope(
        [_session_to_dict(s) for s in sessions],
        meta={"page": page, "per_page": per_page},
    )


@router.get("/{session_id}")
async def get_session_detail(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Get full detail of a session including all variants across all iterations."""
    state = request.app.state.state_store
    session = await state.get_prompt_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Prompt session not found")

    variants = await state.get_prompt_variants_for_session(session_id)
    return _envelope(_session_to_dict(session, variants))


# ════════════════════════════════════════════════════════════════
# Execute (Playground) — streaming endpoint with optional tools
# ════════════════════════════════════════════════════════════════


class ExecuteMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: Any  # str or list of content blocks


class ExecuteRequest(BaseModel):
    system_prompt: str
    messages: list[ExecuteMessage]  # full conversation history including the new user message
    provider: str = "anthropic_sonnet"
    temperature: float = 0.7
    max_tokens: int = 4096
    enable_tools: bool = False


# Tool-use loop max iterations per user turn (model can call tools, see results,
# call more tools, etc., up to this many round trips)
MAX_TOOL_ITERATIONS = 5

# Per-million-token pricing used for the live cost display in the playground.
# Rough estimates; replace with config/thresholds.yaml values if you need accuracy.
PRICING_BY_PROVIDER: dict[str, tuple[float, float]] = {
    "anthropic":        (3.00, 15.00),   # legacy alias → treat as sonnet
    "anthropic_sonnet": (3.00, 15.00),
    "anthropic_opus":   (15.00, 75.00),
    "bedrock":          (3.00, 15.00),
    "openai_gpt5":      (3.00, 15.00),
    "openai_o3":        (15.00, 60.00),
}

# Tool-use hint prepended to system prompt when tools are enabled
TOOL_USAGE_HINT = (
    "You have access to web_search and web_scrape tools. Use them when you need "
    "current information you don't have in your training data (recent news, "
    "current pricing, latest software versions, market data). Don't use them for "
    "general knowledge questions you can answer from training.\n\n"
)


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Events message."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _pick_client_for_execute(request: Request, provider: str) -> tuple[Any, str, str]:
    """Get the (client, model_id, kind) triple for the requested provider.

    `kind` is either "anthropic" or "openai" — tells the streaming generator
    which API surface to use.
    """
    executor = request.app.state.orchestrator._agent_executor
    if not executor:
        raise HTTPException(status_code=503, detail="Agent executor not initialized")

    if provider == "bedrock":
        if not executor.bedrock_client:
            raise HTTPException(
                status_code=400,
                detail="Bedrock requested but AWS credentials not configured",
            )
        model = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-20250514-v1:0")
        return executor.bedrock_client, model, "anthropic"

    if provider == "anthropic_opus":
        if not executor.anthropic_client:
            raise HTTPException(400, "Claude Opus requested but ANTHROPIC_API_KEY not configured")
        return executor.anthropic_client, os.getenv("ANTHROPIC_OPUS_MODEL_ID", "claude-opus-4-6"), "anthropic"

    if provider in ("anthropic", "anthropic_sonnet"):
        if not executor.anthropic_client:
            raise HTTPException(400, "Claude Sonnet requested but ANTHROPIC_API_KEY not configured")
        return executor.anthropic_client, os.getenv("ANTHROPIC_SONNET_MODEL_ID", "claude-sonnet-4-6"), "anthropic"

    if provider == "openai_gpt5":
        if not getattr(executor, "openai_client", None):
            raise HTTPException(400, "OpenAI requested but OPENAI_API_KEY not configured")
        return executor.openai_client, os.getenv("OPENAI_GPT5_MODEL_ID", "gpt-5.4"), "openai"

    if provider == "openai_o3":
        if not getattr(executor, "openai_client", None):
            raise HTTPException(400, "OpenAI requested but OPENAI_API_KEY not configured")
        return executor.openai_client, os.getenv("OPENAI_O3_MODEL_ID", "o4-mini"), "openai"

    raise HTTPException(400, f"Invalid provider '{provider}'")


def _normalize_messages(messages: list[ExecuteMessage]) -> list[dict[str, Any]]:
    """Convert ExecuteMessage objects to the dict format Anthropic SDK expects."""
    return [{"role": m.role, "content": m.content} for m in messages]


async def _execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    """Run a Firecrawl tool and return its string result."""
    if name == "web_search":
        return await WebSearchTool().execute(tool_input)
    if name == "web_scrape":
        return await WebScrapeTool().execute(tool_input)
    return f"Error: unknown tool '{name}'"


def _convert_tools_to_openai_format(anthropic_tools: list[dict]) -> list[dict]:
    """Reshape Anthropic tool schemas into OpenAI function-calling schemas."""
    out: list[dict[str, Any]] = []
    for t in anthropic_tools:
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out


def _cost_for(provider: str, input_tokens: int, output_tokens: int) -> float:
    """Compute the live display cost for a playground run."""
    in_price, out_price = PRICING_BY_PROVIDER.get(provider, (3.0, 15.0))
    return round(
        (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price,
        6,
    )


async def _stream_execute_anthropic(
    client: Any,
    model: str,
    body: ExecuteRequest,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
    started_at: float,
) -> AsyncGenerator[str, None]:
    """Anthropic Messages API streaming path — preserved from the original impl."""
    total_input_tokens = 0
    total_output_tokens = 0
    iterations = 0

    while iterations < MAX_TOOL_ITERATIONS:
        iterations += 1
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": body.max_tokens,
            "temperature": body.temperature,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                et = getattr(event, "type", None)
                if et == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta and getattr(delta, "type", None) == "text_delta":
                        yield _sse_event("text_delta", {"text": delta.text})
                elif et == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block and getattr(block, "type", None) == "tool_use":
                        yield _sse_event("tool_use_start", {
                            "id": block.id,
                            "name": block.name,
                        })

            final_message = await stream.get_final_message()

        usage = getattr(final_message, "usage", None)
        if usage:
            total_input_tokens += getattr(usage, "input_tokens", 0)
            total_output_tokens += getattr(usage, "output_tokens", 0)

        tool_use_blocks = [
            b for b in final_message.content
            if getattr(b, "type", None) == "tool_use"
        ]
        if not tool_use_blocks:
            break

        messages.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": b.text} if b.type == "text"
                else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                for b in final_message.content
            ],
        })

        tool_results: list[dict[str, Any]] = []
        for tu in tool_use_blocks:
            result_text = await _execute_tool(tu.name, tu.input)
            yield _sse_event("tool_use_result", {
                "id": tu.id,
                "name": tu.name,
                "input": tu.input,
                "result_preview": result_text[:600],
                "result_chars": len(result_text),
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_results})

    elapsed_ms = int((time.time() - started_at) * 1000)
    yield _sse_event("message_complete", {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cost_usd": _cost_for(body.provider, total_input_tokens, total_output_tokens),
        "latency_ms": elapsed_ms,
        "iterations": iterations,
    })


async def _stream_execute_openai(
    client: Any,
    model: str,
    body: ExecuteRequest,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
    started_at: float,
) -> AsyncGenerator[str, None]:
    """OpenAI Chat Completions path — non-streaming tool loop.

    For MVP we use non-streaming (await the full response per iteration) and then
    emit the assistant text as one large `text_delta` SSE event. This keeps the
    frontend event protocol identical and avoids the complexity of parsing
    OpenAI's partial tool_call deltas from a streaming chunk generator.
    """
    # Convert Anthropic tool schemas to OpenAI function format
    oai_tools = _convert_tools_to_openai_format(tools) if tools else None

    # Build the initial OpenAI message list: system first, then the conversation
    oai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    for m in messages:
        content = m.get("content")
        # The playground frontend sends content as either a string or a list of
        # blocks. Flatten block lists to text for OpenAI.
        if isinstance(content, list):
            text_parts = [
                blk.get("text", "") for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text"
            ]
            content = "\n".join(p for p in text_parts if p)
        oai_messages.append({"role": m.get("role", "user"), "content": content or ""})

    is_reasoning = model.startswith("o") and not model.startswith("openai")
    total_input_tokens = 0
    total_output_tokens = 0
    iterations = 0

    while iterations < MAX_TOOL_ITERATIONS:
        iterations += 1
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
        }
        if is_reasoning:
            kwargs["max_completion_tokens"] = body.max_tokens
            # reasoning models don't accept custom temperature
        else:
            kwargs["max_tokens"] = body.max_tokens
            kwargs["temperature"] = body.temperature
        if oai_tools:
            kwargs["tools"] = oai_tools

        response = await client.chat.completions.create(**kwargs)

        usage = getattr(response, "usage", None)
        if usage:
            total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
            total_output_tokens += getattr(usage, "completion_tokens", 0) or 0

        msg = response.choices[0].message
        text = msg.content or ""
        tool_calls = msg.tool_calls or []

        # Emit any text the model produced this turn
        if text:
            yield _sse_event("text_delta", {"text": text})

        if not tool_calls:
            break

        # Append the assistant turn (with tool_calls) and execute each tool
        oai_assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": text if text else None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ],
        }
        oai_messages.append(oai_assistant_msg)

        for tc in tool_calls:
            raw_args = tc.function.arguments or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {}

            yield _sse_event("tool_use_start", {
                "id": tc.id,
                "name": tc.function.name,
            })

            result_text = await _execute_tool(tc.function.name, parsed_args)

            yield _sse_event("tool_use_result", {
                "id": tc.id,
                "name": tc.function.name,
                "input": parsed_args,
                "result_preview": result_text[:600],
                "result_chars": len(result_text),
            })

            oai_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_text,
            })

    elapsed_ms = int((time.time() - started_at) * 1000)
    yield _sse_event("message_complete", {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cost_usd": _cost_for(body.provider, total_input_tokens, total_output_tokens),
        "latency_ms": elapsed_ms,
        "iterations": iterations,
    })


async def _stream_execute(
    request: Request,
    body: ExecuteRequest,
) -> AsyncGenerator[str, None]:
    """Top-level dispatcher — picks the Anthropic or OpenAI streaming path."""
    started_at = time.time()
    client, model, kind = _pick_client_for_execute(request, body.provider)

    system_prompt = body.system_prompt or ""
    if body.enable_tools:
        system_prompt = TOOL_USAGE_HINT + system_prompt

    tools: list[dict] | None = None
    if body.enable_tools:
        tools = [WebSearchTool().schema(), WebScrapeTool().schema()]

    messages = _normalize_messages(body.messages)

    yield _sse_event("turn_start", {"provider": body.provider, "model": model})

    try:
        if kind == "openai":
            async for evt in _stream_execute_openai(
                client, model, body, system_prompt, messages, tools, started_at
            ):
                yield evt
        else:
            async for evt in _stream_execute_anthropic(
                client, model, body, system_prompt, messages, tools, started_at
            ):
                yield evt
    except Exception as e:
        logger.exception("execute_stream_error")
        yield _sse_event("error", {"message": str(e)})

    yield _sse_event("done", {})


@router.post("/execute/stream")
async def execute_stream(
    body: ExecuteRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Stream a multi-turn execution against the LLM with optional Firecrawl tools.

    Returns Server-Sent Events. Event types:
      - turn_start:        {provider, model}
      - text_delta:        {text}                — append to current assistant text
      - tool_use_start:    {id, name}            — model is about to call a tool
      - tool_use_result:   {id, name, input, result_preview, result_chars}
      - message_complete:  {input_tokens, output_tokens, cost_usd, latency_ms, iterations}
      - error:             {message}
      - done:              {}                    — stream is over
    """
    if not body.system_prompt and not body.messages:
        raise HTTPException(400, "Either system_prompt or messages must be provided")
    _validate_provider(body.provider)

    return StreamingResponse(
        _stream_execute(request, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if behind a proxy
            "Connection": "keep-alive",
        },
    )
