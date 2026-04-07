"""Prompt Studio endpoints — generate, refine, select, history, templates."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import get_current_user
from src.core.prompt_engineer import PromptEngineer, PromptEngineerError
from src.models.base import PromptSession, PromptVariant

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])

TEMPLATES_PATH = Path("config/prompt_templates.yaml")


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
    )


# ── Request/Response Schemas ─────────────────────


class GenerateRequest(BaseModel):
    use_case: str
    target_audience: str = ""
    desired_output: str = ""
    tone: str = ""
    constraints: str = ""
    options: dict[str, Any] = {}
    provider: str = "anthropic"
    template_id: str | None = None


class RefineRequest(BaseModel):
    feedback: str
    provider: str = "anthropic"


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
    if body.provider not in ("anthropic", "bedrock"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{body.provider}'. Must be 'anthropic' or 'bedrock'.",
        )

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
