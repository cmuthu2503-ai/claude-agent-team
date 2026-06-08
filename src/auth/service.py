"""Auth service — JWT tokens, password hashing, RBAC middleware."""

import hashlib
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import bcrypt
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.models.base import User, UserRole
from src.state.base import StateStore

logger = structlog.get_logger()

security_scheme = HTTPBearer(auto_error=False)


class AuthService:
    """Handles authentication, token management, and user operations."""

    def __init__(
        self,
        state: StateStore,
        secret_key: str = "dev-secret-change-in-production",
        algorithm: str = "HS256",
        access_token_minutes: int = 30,
        refresh_token_days: int = 7,
    ) -> None:
        self.state = state
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_minutes = access_token_minutes
        self.refresh_token_days = refresh_token_days

    def hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def verify_password(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode(), hashed.encode())

    def create_access_token(self, user: User) -> str:
        expire = datetime.utcnow() + timedelta(minutes=self.access_token_minutes)
        payload = {
            "sub": user.user_id,
            "username": user.username,
            "role": user.role,
            # KB-29 — curator scopes travel in the token so route guards don't
            # have to hit the DB on every curation action.
            "kb_curator_scopes": list(user.kb_curator_scopes or []),
            "exp": expire,
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self) -> str:
        return secrets.token_urlsafe(64)

    def decode_token(self, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
            )

    async def authenticate(self, username: str, password: str) -> tuple[User, str]:
        result = await self.state.get_user_by_username(username)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )
        user, password_hash = result
        if not self.verify_password(password, password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )
        user.last_login_at = datetime.utcnow()
        await self.state.update_user(user)
        access_token = self.create_access_token(user)
        return user, access_token

    async def bootstrap_admin(self) -> str | None:
        existing = await self.state.get_user_by_username("admin")
        if existing:
            return None
        password = secrets.token_urlsafe(16)
        admin = User(
            user_id=str(uuid.uuid4()),
            username="admin",
            email="admin@agent-team.local",
            role=UserRole.ADMIN,
            must_change_password=True,
        )
        await self.state.create_user(admin, self.hash_password(password))
        logger.info("admin_bootstrapped", username="admin", password=password)
        return password


def require_role(*allowed_roles: str):
    """FastAPI dependency that checks the user's role."""
    async def role_checker(
        credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
        request: Request = None,
    ) -> dict[str, Any]:
        if not credentials:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        auth_service: AuthService = request.app.state.auth_service
        payload = auth_service.decode_token(credentials.credentials)
        user_role = payload.get("role", "")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user_role}' not authorized. Required: {allowed_roles}",
            )
        return payload
    return role_checker


def is_kb_curator(payload: dict[str, Any], scope: str | None = None) -> bool:
    """KB-29 — does this user hold the curator capability for ``scope``?

    A scope is ``"platform"`` (the kb_platform corpus) or a ``project_id`` (that
    app's KB/memory). Admins are implicit curators everywhere; developers are
    implicit curators too (they could already approve docs pre-KB-29, so this
    is backward-compatible). Beyond those, a user qualifies if their granted
    ``kb_curator_scopes`` contains ``scope`` or the wildcard ``"*"`` — this is
    how a plain viewer is made curator of a single project without elevating
    their whole role.
    """
    if payload.get("role") in ("admin", "developer"):
        return True
    scopes = payload.get("kb_curator_scopes") or []
    if "*" in scopes:
        return True
    return scope is not None and scope in scopes


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    request: Request = None,
) -> dict[str, Any]:
    """FastAPI dependency that returns the current user payload."""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    auth_service: AuthService = request.app.state.auth_service
    return auth_service.decode_token(credentials.credentials)


# ── Service-token auth (HAI-02 / FR-010..012) ────────────────────────────────


def hash_service_token(raw: str) -> str:
    """SHA-256 hex digest of a raw service token — the form persisted in the
    ``service_tokens`` table. The raw token is never stored; auth re-hashes the
    presented value and looks the hash up."""
    return hashlib.sha256(raw.encode()).hexdigest()


# HAI-52 (FR-015) — last_used_at write debounce. A chatty service client (the
# Hermes scheduler polling every few seconds) would otherwise write to the
# shared SQLite DB on every request, contending with the host supervisor. We
# coalesce to at most one write per token per window. Process-local: each
# backend process writes ≤ once/window/token, which is plenty for telemetry.
_LAST_USED_DEBOUNCE_SECONDS = 60.0
_last_used_touched: dict[str, float] = {}


def _should_touch_last_used(token_id: str, now: float | None = None) -> bool:
    """Return True if ``last_used_at`` should be written for ``token_id`` now —
    i.e. it hasn't been written within the debounce window. Records the touch
    time as a side effect. ``now`` is injectable for tests."""
    current = now if now is not None else time.monotonic()
    last = _last_used_touched.get(token_id)
    if last is not None and (current - last) < _LAST_USED_DEBOUNCE_SECONDS:
        return False
    _last_used_touched[token_id] = current
    return True


async def get_service_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    request: Request = None,
) -> dict[str, Any]:
    """FastAPI dependency: authenticate a long-lived SERVICE token presented as
    ``Authorization: Bearer <token>`` (HAI-02, used by the headless Hermes Agent
    integration).

    Returns a principal payload shaped like a user payload (``sub`` / ``role`` /
    ``kb_curator_scopes``) so existing route guards can treat it uniformly, but
    tagged ``is_service_token=True`` so later layers can tell a MACHINE principal
    from a human (the write-block FR-015a and the human-only proposal confirm
    FR-038 both rely on this flag). Unknown and revoked tokens are rejected 401.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    state: StateStore = request.app.state.state_store
    token = await state.get_service_token_by_hash(hash_service_token(credentials.credentials))
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token"
        )
    if token.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Service token has been revoked"
        )
    # Stamp usage — debounced (HAI-52) to ≤ once/window/token so a chatty client
    # doesn't write to the shared SQLite DB on every request. Best-effort
    # telemetry: a write hiccup must NOT block auth (mirrors the resolver's
    # "DB blip can't break dispatch" rule).
    if _should_touch_last_used(token.token_id):
        try:
            await state.touch_service_token_last_used(token.token_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("service_token_touch_failed", token_id=token.token_id, error=str(e))

    return {
        "sub": token.token_id,
        "token_id": token.token_id,
        "username": token.name,
        "role": str(token.role),
        "kb_curator_scopes": [],
        "is_service_token": True,
    }
