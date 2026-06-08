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


async def _resolve_service_principal(raw: str, state: StateStore) -> dict[str, Any] | None:
    """Resolve a raw bearer to a SERVICE principal, or None if it isn't a service
    token (so callers can fall back to JWT). Raises 401 for a *revoked* token.

    Side effects (debounced, HAI-52): stamps ``last_used_at`` and emits the
    bounded ``service_principal_active`` attribution log (HAI-04 / FR-082).
    """
    token = await state.get_service_token_by_hash(hash_service_token(raw))
    if token is None:
        return None
    if token.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Service token has been revoked"
        )
    if _should_touch_last_used(token.token_id):
        logger.info(
            "service_principal_active",
            actor=f"service:{token.name}",
            token_id=token.token_id,
            role=str(token.role),
        )
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


async def get_service_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    request: Request = None,
) -> dict[str, Any]:
    """FastAPI dependency: authenticate a long-lived SERVICE token only (rejects
    JWTs). Used where only a service identity makes sense (e.g. /service-tokens/me).

    Returns a principal payload shaped like a user payload, tagged
    ``is_service_token=True`` so later layers can tell a MACHINE principal from a
    human (write-block FR-015a, human-only confirm FR-038). Unknown/revoked → 401.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    state: StateStore = request.app.state.state_store
    principal = await _resolve_service_principal(credentials.credentials, state)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token"
        )
    return principal


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    request: Request = None,
) -> dict[str, Any]:
    """FastAPI dependency accepting EITHER a human JWT or a service token,
    returning a uniform principal (HAI-10). Read endpoints the MCP monitor tools
    call use this so the headless Hermes service token can read them, while human
    UI traffic keeps working unchanged. The write-block (FR-015a) still blocks any
    state-changing call a service principal makes, so broadening reads is safe.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    state: StateStore = request.app.state.state_store
    # Cheap hash lookup first; a human JWT hashes to nothing in service_tokens.
    principal = await _resolve_service_principal(credentials.credentials, state)
    if principal is not None:
        return principal
    auth_service: AuthService = request.app.state.auth_service
    return auth_service.decode_token(credentials.credentials)


def principal_actor(principal: dict[str, Any]) -> str:
    """Canonical actor string for attribution — ``created_by`` / ``proposed_by``
    / audit logs — uniform across human and service principals (HAI-04 / FR-013,
    FR-082). The proposals engine (P2) and any service-originated row stamp this
    so Hermes-driven actions are distinguishable from human ones.

    - Service token → ``service:<name>`` (falls back to the token id).
    - Human user    → username (falls back to ``sub``).
    """
    if principal.get("is_service_token"):
        return f"service:{principal.get('username') or principal.get('token_id') or 'unknown'}"
    return principal.get("username") or principal.get("sub") or "unknown"
