"""Auth service — JWT tokens, password hashing, RBAC middleware."""

import secrets
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    request: Request = None,
) -> dict[str, Any]:
    """FastAPI dependency that returns the current user payload."""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    auth_service: AuthService = request.app.state.auth_service
    return auth_service.decode_token(credentials.credentials)
