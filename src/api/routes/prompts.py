"""Prompt Studio endpoints — generate, refine, select, history, templates, execute.

All LLM calls go through the AnthropicAWS client (Claude Platform on AWS) using
the same opus-4-7 model the agents use.
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.auth.service import get_current_user
from src.core.prompt_engineer import PromptEngineer, PromptEngineerError
from src.models.base import PromptSession, PromptVariant
from src.tools.firecrawl_tools import WebScrapeTool, WebSearchTool

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])

TEMPLATES_PATH = Path("config/prompt_templates.yaml")

# Default model for the Prompt Studio. Overridable via env for short-term experiments.
PROMPT_STUDIO_MODEL = os.getenv("PROMPT_STUDIO_MODEL", "claude-opus-4-7")

# Per-million-token pricing for the live cost display in the playground.
# Mirror the values in config/thresholds.yaml (Claude Platform on AWS, US geo).
PROMPT_STUDIO_INPUT_PRICE = float(os.getenv("PROMPT_STUDIO_INPUT_PRICE", "16.50"))
PROMPT_STUDIO_OUTPUT_PRICE = float(os.getenv("PROMPT_STUDIO_OUTPUT_PRICE", "82.50"))


def _envelope(data: Any, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta, "error": None}


def _get_executor(request: Request) -> Any:
    """Return the AgentSystemExecutor or raise 503 if not initialized."""
    executor = request.app.state.orchestrator._agent_executor
    if not executor:
        raise HTTPException(
            status_code=503,
            detail="Agent executor not initialized — LLM client unavailable",
        )
    return executor


def _get_engineer(request: Request) -> PromptEngineer:
    """Build a PromptEngineer wired to the live AnthropicAWS client."""
    executor = _get_executor(request)
    return PromptEngineer(
        anthropic_client=executor.anthropic_client,
        model=PROMPT_STUDIO_MODEL,
        inference_geo=executor.inference_geo,
    )


# ── Request/Response Schemas ─────────────────────


class GenerateRequest(BaseModel):
    use_case: str
    target_audience: str = ""
    desired_output: str = ""
    tone: str = ""
    constraints: str = ""
    options: dict[str, Any] = {}
    template_id: str | None = None


class RefineRequest(BaseModel):
    feedback: str


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


def _session_to_dict(
    s: PromptSession, variants: list[PromptVariant] | None = None,
) -> dict[str, Any]:
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
    """Build a single polished prompt from the structured inputs."""
    if not body.use_case or not body.use_case.strip():
        raise HTTPException(status_code=400, detail="use_case is required")

    state = request.app.state.state_store
    engineer = _get_engineer(request)
    user_id = user.get("user_id") or user.get("username", "unknown")

    session = PromptSession(
        session_id=f"PS-{uuid.uuid4().hex[:8].upper()}",
        user_id=user_id,
        use_case=body.use_case,
        target_audience=body.target_audience,
        desired_output=body.desired_output,
        tone=body.tone,
        constraints=body.constraints,
        options=body.options,
        template_id=body.template_id,
    )
    await state.create_prompt_session(session)

    try:
        variant_dicts = await engineer.generate_variants(
            use_case=body.use_case,
            target_audience=body.target_audience,
            desired_output=body.desired_output,
            tone=body.tone,
            constraints=body.constraints,
            options=body.options,
        )
    except PromptEngineerError as e:
        logger.error("prompt_generate_failed", session_id=session.session_id, error=str(e))
        raise HTTPException(status_code=502, detail=str(e))

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

    all_variants = await state.get_prompt_variants_for_session(session_id)
    selected = next(
        (v for v in all_variants if v.variant_id == session.selected_variant_id),
        None,
    )
    if not selected:
        raise HTTPException(status_code=404, detail="Selected variant not found")

    max_iter = max((v.iteration for v in all_variants), default=0)
    next_iteration = max_iter + 1

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
        )
    except PromptEngineerError as e:
        logger.error("prompt_refine_failed", session_id=session_id, error=str(e))
        raise HTTPException(status_code=502, detail=str(e))

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
    messages: list[ExecuteMessage]
    temperature: float = 0.7
    max_tokens: int = 4096
    enable_tools: bool = False


MAX_TOOL_ITERATIONS = 5

TOOL_USAGE_HINT = (
    "You have access to web_search and web_scrape tools. Use them when you need "
    "current information you don't have in your training data (recent news, "
    "current pricing, latest software versions, market data). Don't use them for "
    "general knowledge questions you can answer from training.\n\n"
)


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _normalize_messages(messages: list[ExecuteMessage]) -> list[dict[str, Any]]:
    return [{"role": m.role, "content": m.content} for m in messages]


async def _execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    if name == "web_search":
        return await WebSearchTool().execute(tool_input)
    if name == "web_scrape":
        return await WebScrapeTool().execute(tool_input)
    return f"Error: unknown tool '{name}'"


def _cost_for(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1_000_000) * PROMPT_STUDIO_INPUT_PRICE
        + (output_tokens / 1_000_000) * PROMPT_STUDIO_OUTPUT_PRICE,
        6,
    )


async def _stream_execute(
    request: Request,
    body: ExecuteRequest,
) -> AsyncGenerator[str, None]:
    """Stream a multi-turn conversation against Claude Platform on AWS.

    Supports optional Firecrawl tools (web_search / web_scrape).
    """
    started_at = time.time()
    executor = _get_executor(request)
    client = executor.anthropic_client
    if not client:
        yield _sse_event("error", {
            "message": "Claude Platform on AWS client not configured. "
                       "Set ANTHROPIC_AWS_API_KEY and ANTHROPIC_AWS_WORKSPACE_ID.",
        })
        yield _sse_event("done", {})
        return

    model = PROMPT_STUDIO_MODEL
    inference_geo = executor.inference_geo

    system_prompt = body.system_prompt or ""
    if body.enable_tools:
        system_prompt = TOOL_USAGE_HINT + system_prompt

    tools: list[dict] | None = None
    if body.enable_tools:
        tools = [WebSearchTool().schema(), WebScrapeTool().schema()]

    messages = _normalize_messages(body.messages)

    yield _sse_event("turn_start", {"model": model})

    total_input_tokens = 0
    total_output_tokens = 0
    iterations = 0

    try:
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
            if inference_geo:
                kwargs["inference_geo"] = inference_geo

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
            "cost_usd": _cost_for(total_input_tokens, total_output_tokens),
            "latency_ms": elapsed_ms,
            "iterations": iterations,
        })
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
    """Stream a multi-turn execution against Claude Platform on AWS with optional Firecrawl tools.

    Returns Server-Sent Events. Event types:
      - turn_start:        {model}
      - text_delta:        {text}
      - tool_use_start:    {id, name}
      - tool_use_result:   {id, name, input, result_preview, result_chars}
      - message_complete:  {input_tokens, output_tokens, cost_usd, latency_ms, iterations}
      - error:             {message}
      - done:              {}
    """
    if not body.system_prompt and not body.messages:
        raise HTTPException(400, "Either system_prompt or messages must be provided")

    return StreamingResponse(
        _stream_execute(request, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
