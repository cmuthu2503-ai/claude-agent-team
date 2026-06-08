"""Service-token write-block middleware (HAI-51 / FR-015a).

The security backstop for the headless Hermes Agent identity. A request
authenticated by a SERVICE token may only ever *create a proposal*
(``POST /api/v1/proposals``) to request a state change — it must NEVER reach an
underlying write endpoint directly, **regardless of that route's own guard**.

This is enforced globally here (a middleware), not per-route, precisely because
FR-011 found the existing project write endpoints are ``get_current_user``-only:
relying on route guards would leave gaps. With this block in place, the only way
a service principal can mutate state is through the (separately human-confirmed)
proposal flow.

Cost: one ``service_tokens`` hash lookup per *state-changing* request that
carries a bearer token. GET/HEAD/OPTIONS skip the check entirely. JWT (human)
tokens hash to nothing in ``service_tokens`` and pass straight through to their
own route guards.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from src.auth.service import hash_service_token

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

# The single write a service principal is allowed: creating a proposal. Confirm/
# reject (POST /api/v1/proposals/{id}/confirm|reject) are human-only (FR-038) and
# are deliberately NOT matched here, so a service token is blocked from them too.
_PROPOSAL_CREATE_PATH = "/api/v1/proposals"


def _is_allowed_service_write(method: str, path: str) -> bool:
    return method == "POST" and path.rstrip("/") == _PROPOSAL_CREATE_PATH


class ServiceTokenWriteBlockMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests made with a service token unless they are
    proposal creation. Read requests and human (JWT) requests are untouched."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.method not in _STATE_CHANGING:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return await call_next(request)

        raw = auth[7:].strip()
        state = getattr(request.app.state, "state_store", None)
        if not raw or state is None:
            return await call_next(request)

        # Is this bearer a *service* token? (A human JWT hashes to nothing here.)
        try:
            token = await state.get_service_token_by_hash(hash_service_token(raw))
        except Exception:  # noqa: BLE001
            # A lookup blip must not block legitimate human traffic; fall through
            # and let the route's own auth decide.
            return await call_next(request)

        # Only live service tokens are blocked here; a revoked one will 401 at the
        # route's auth dependency anyway.
        if token is not None and not token.is_revoked:
            if not _is_allowed_service_write(request.method, request.url.path):
                return JSONResponse(
                    status_code=403,
                    content={
                        "data": None,
                        "meta": None,
                        "error": {
                            "code": "service_token_write_forbidden",
                            "message": (
                                "Service tokens cannot call state-changing endpoints "
                                "directly. Create a proposal (POST /api/v1/proposals) "
                                "and have a human approve it."
                            ),
                        },
                    },
                )

        return await call_next(request)
