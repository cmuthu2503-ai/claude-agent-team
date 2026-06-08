"""Service-token management endpoints — admin only (HAI-03 / FR-012).

Long-lived headless identities (e.g. the Hermes Agent integration). The RAW
token is returned exactly ONCE at creation and is never retrievable again — only
its SHA-256 hash is stored. Issue/list/revoke are admin-gated.
"""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import hash_service_token, require_role
from src.models.base import ServiceToken, UserRole

router = APIRouter(prefix="/api/v1/service-tokens", tags=["service-tokens"])

_VALID_ROLES = {r.value for r in UserRole}


class CreateServiceTokenBody(BaseModel):
    name: str
    role: str = "viewer"


def _public(t: ServiceToken) -> dict:
    """Serialize a token for the API — never includes the hash or raw value."""
    return {
        "token_id": t.token_id,
        "name": t.name,
        "role": str(t.role),
        "created_at": t.created_at.isoformat(),
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
        "revoked": t.is_revoked,
    }


@router.get("")
async def list_service_tokens(
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    """List all service tokens (metadata only — no secret material)."""
    state = request.app.state.state_store
    tokens = await state.list_service_tokens()
    return {"data": [_public(t) for t in tokens], "meta": None, "error": None}


@router.post("", status_code=201)
async def create_service_token(
    body: CreateServiceTokenBody,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    """Issue a new service token. The raw token is returned in this response
    ONLY — it is hashed (SHA-256) for storage and can never be retrieved again."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if body.role not in _VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role {body.role!r}. Must be one of: {sorted(_VALID_ROLES)}",
        )
    state = request.app.state.state_store
    token_id = f"stok-{uuid.uuid4().hex[:12]}"
    raw = f"hermes_{secrets.token_urlsafe(40)}"
    await state.create_service_token(token_id, name, hash_service_token(raw), body.role)
    return {
        "data": {
            "token_id": token_id,
            "name": name,
            "role": body.role,
            # Shown exactly once — the caller MUST persist it now.
            "token": raw,
        },
        "meta": {
            "warning": "Store this token now — it is shown only once and cannot be retrieved.",
        },
        "error": None,
    }


@router.delete("/{token_id}", status_code=204)
async def revoke_service_token(
    token_id: str,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    """Revoke a token. 204 on success; 404 if it doesn't exist or was already
    revoked (revoke is idempotent at the store layer)."""
    state = request.app.state.state_store
    revoked = await state.revoke_service_token(token_id)
    if not revoked:
        raise HTTPException(
            status_code=404, detail="Service token not found or already revoked"
        )
    # 204 No Content — no body.
