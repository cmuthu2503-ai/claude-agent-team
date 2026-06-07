"""Auth endpoints — login, refresh, logout, me."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from src.auth.service import AuthService, get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=1024)


class ChangePasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(..., min_length=1, max_length=1024)


class RefreshBody(BaseModel):
    # Refresh token is delivered via httpOnly cookie, so the body is
    # expected to be empty. `extra="forbid"` rejects pen-test probes
    # that try to slip in NoSQL operator objects ({"$ne": null}).
    model_config = ConfigDict(extra="forbid")


class LogoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    auth: AuthService = request.app.state.auth_service
    user, access_token = await auth.authenticate(body.username, body.password)
    refresh_token = auth.create_refresh_token()
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=7 * 24 * 3600,
        samesite="lax",
    )
    return {
        "data": {
            "access_token": access_token,
            "expires_in": auth.access_token_minutes * 60,
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "role": user.role,
                "must_change_password": user.must_change_password,
            },
        },
        "meta": None,
        "error": None,
    }


@router.post("/refresh")
async def refresh(request: Request, body: RefreshBody | None = None):
    # Simplified refresh — in production, validate refresh token from cookie.
    # `body` is declared (with extra="forbid") so unexpected payloads —
    # e.g. NoSQL operator objects like {"username": {"$ne": null}} — are
    # rejected with 422 before reaching this handler.
    _ = body
    return {"data": {"message": "Token refresh not yet implemented"}, "meta": None, "error": None}


@router.post("/logout")
async def logout(response: Response, body: LogoutBody | None = None):
    _ = body
    response.delete_cookie("refresh_token")
    return {"data": {"message": "Logged out"}, "meta": None, "error": None}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"data": user, "meta": None, "error": None}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    auth: AuthService = request.app.state.auth_service
    state = request.app.state.state_store
    new_hash = auth.hash_password(body.new_password)
    await state.update_password(user["sub"], new_hash)
    return {"data": {"message": "Password changed"}, "meta": None, "error": None}
