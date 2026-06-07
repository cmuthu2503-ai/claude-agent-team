"""User management endpoints — admin only."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import AuthService, require_role
from src.models.base import User, UserRole

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class CreateUserBody(BaseModel):
    username: str
    email: str
    role: str = "developer"
    password: str
    # KB-29 — optional curator scopes ("platform" | project_id | "*").
    kb_curator_scopes: list[str] | None = None


class UpdateUserBody(BaseModel):
    email: str | None = None
    role: str | None = None
    is_active: bool | None = None
    # KB-29 — admin grants/revokes the curator capability per scope here.
    kb_curator_scopes: list[str] | None = None


@router.get("")
async def list_users(
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    state = request.app.state.state_store
    users = await state.list_users()
    return {
        "data": [
            {
                "user_id": u.user_id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "kb_curator_scopes": u.kb_curator_scopes,
                "created_at": u.created_at.isoformat(),
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in users
        ],
        "meta": None,
        "error": None,
    }


@router.post("")
async def create_user(
    body: CreateUserBody,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    auth: AuthService = request.app.state.auth_service
    state = request.app.state.state_store
    user = User(
        user_id=str(uuid.uuid4()),
        username=body.username,
        email=body.email,
        role=UserRole(body.role),
        kb_curator_scopes=body.kb_curator_scopes or [],
    )
    password_hash = auth.hash_password(body.password)
    await state.create_user(user, password_hash)
    return {"data": {"user_id": user.user_id, "username": user.username}, "meta": None, "error": None}


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserBody,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    state = request.app.state.state_store
    user = await state.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.email is not None:
        user.email = body.email
    if body.role is not None:
        user.role = UserRole(body.role)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.kb_curator_scopes is not None:
        user.kb_curator_scopes = body.kb_curator_scopes
    await state.update_user(user)
    return {"data": {"user_id": user.user_id, "updated": True}, "meta": None, "error": None}
