"""Model catalog endpoints — PAM-12.

Surfaces the runtime model catalog (``config/models.yaml``, loaded into
``AgentSystemExecutor.model_catalog`` at boot) over HTTP so:

  - The Team Status page can populate the per-agent model dropdown with
    real catalog ids + pretty display names + tier badges, instead of
    hard-coding model strings in the frontend.

  - Operators can hot-reload the catalog after editing ``models.yaml``
    on disk (admin-only, ``POST /api/v1/models/reload``) without
    restarting the backend container.

Roles
-----
- ``GET /api/v1/models`` — any authenticated user (viewer+). The
  catalog is operational config, not secret; viewers need it so the
  read-only Team Status view can render model labels.
- ``POST /api/v1/models/reload`` — admin only. Reload rebuilds the
  ``LLMClientPool`` keys implicitly (clients are cached lazily), and
  swaps in the new catalog on the live ``ModelResolver``. A reload
  with an invalid YAML file fails LOUDLY (returns 422 with the
  pydantic error chain) so the operator can fix-and-retry without
  ever putting a broken catalog into the resolver.

Behaviour notes
---------------
- Pricing is exposed as cents-per-million tokens for both input and
  output, matching the catalog dataclass field names. The cost
  dashboard (PAM-15) reads the same source, so a `$0.00` row in the
  ticker means "we resolved this model but its catalog entry is
  missing pricing" — fix the YAML, hit reload, refresh.

- The legacy alias map (``legacy_provider_aliases``) is NOT exposed
  on the GET response — it's an internal back-compat detail for old
  persisted Request.provider rows, not a user-facing catalog feature.
  Surfacing it would invite operators to write code that depends on
  those strings, which we want to deprecate as PR-5 lands.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth.service import get_current_user, require_role

router = APIRouter(prefix="/api/v1/models", tags=["models"])


def _model_to_dict(model: Any) -> dict[str, Any]:
    """Serialise a ``ModelDef`` into the response shape the frontend
    expects. Centralised so the GET and reload routes return the same
    surface — if we add a field here it lands in both places."""
    return {
        "id": model.id,
        "provider_type": model.provider_type,
        "model_id": model.model_id,
        "display_name": model.display_name,
        "tier": model.tier,
        "tool_calling_mode": model.tool_calling_mode,
        # ``base_url`` lets the UI distinguish "cloud openai_compat"
        # from "self-hosted openai_compat" (Ollama) without parsing
        # the id. Null for providers that don't take a base_url.
        "base_url": model.base_url,
        "pricing_per_million": {
            "input": model.pricing_per_million.input,
            "output": model.pricing_per_million.output,
        },
    }


@router.get("")
async def list_models(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the loaded model catalog.

    Falls back to a 503 when the executor's catalog isn't wired (the
    PAM-07 soft-fail path — happens when ``config/models.yaml`` failed
    to parse at boot). That's a deliberate 503 not a 500: it tells the
    operator "the platform is up but a config artifact is broken,"
    rather than a hard crash that looks like a code bug.
    """
    executor = getattr(request.app.state, "agent_executor", None)
    catalog = getattr(executor, "model_catalog", None) if executor else None
    if catalog is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "model catalog is not loaded — config/models.yaml failed "
                "to parse at boot. Check backend logs for the validation "
                "error and POST /api/v1/models/reload after fixing."
            ),
        )

    models = [_model_to_dict(m) for m in catalog.list_all()]
    return {
        "data": {
            "default_model": catalog.default_model,
            "models": models,
        },
        "meta": {"count": len(models)},
        "error": None,
    }


@router.post("/reload")
async def reload_catalog(
    request: Request,
    user: dict = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Re-read ``config/models.yaml`` and swap it onto the live
    ``ModelResolver``. Admin-only.

    Failure modes
    -------------
    - YAML missing / unreadable → 422 with the OS error.
    - YAML parses but fails pydantic validation → 422 with the
      pydantic error chain (operator copies it back into their YAML
      and retries).
    - Reload succeeds → 200 with the new model count + the previous
      count, so the operator can sanity-check (e.g. "I expected to
      add 1, count went from 7 to 8").

    The reload is **best-effort**: if validation fails we DO NOT
    touch the live resolver — the old catalog stays in effect and
    in-flight dispatches keep working. This is the L25 INSUFFICIENT_DATA
    pattern: a broken config file is a known fallback, not a crash.
    """
    from src.models.catalog import ModelCatalog, default_catalog_path

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None or executor.model_resolver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "executor / resolver not wired — there's no live catalog "
                "to replace. Restart the backend to pick up models.yaml."
            ),
        )

    previous_count = len(executor.model_catalog.models)

    try:
        new_catalog = ModelCatalog.load(default_catalog_path())
    except Exception as e:  # noqa: BLE001
        # Validation / IO error — surface it verbatim so the operator
        # can fix the YAML without trawling logs. 422 (Unprocessable
        # Entity) is the right code: the request is well-formed, the
        # YAML on disk isn't.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"catalog reload failed: {e}",
        ) from e

    # Swap atomically — both references move together so a concurrent
    # resolve() call sees one or the other, never a half-updated state.
    executor.model_catalog = new_catalog
    executor.model_resolver.catalog = new_catalog

    return {
        "data": {
            "default_model": new_catalog.default_model,
            "previous_count": previous_count,
            "new_count": len(new_catalog.models),
        },
        "meta": None,
        "error": None,
    }
