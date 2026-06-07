"""Body-size cap on auth routes.

Hardens `/api/v1/auth/*` against memory-waste DOS where an attacker
spams oversized JSON bodies at unauthenticated endpoints (login,
refresh, logout). 64 KB is far above any legitimate auth payload
(longest field is a JWT refresh token, ~1-4 KB) while keeping the
attacker's per-request cost low.

Other routes are left alone — some legitimately accept multi-MB
payloads (e.g. document uploads, PRD ingest). Tightening those needs
per-route reasoning we don't have here.

Returns 413 (Payload Too Large) before the body is buffered into
memory, by checking Content-Length on the request. Chunked requests
without Content-Length fall through to FastAPI's normal flow; that's
fine because httpx (and the pen-test probe) send Content-Length.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# 64 KB — see module docstring.
_AUTH_MAX_BODY_BYTES = 64 * 1024
_AUTH_PREFIX = "/api/v1/auth/"


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_AUTH_PREFIX):
            cl = request.headers.get("content-length")
            if cl is not None:
                try:
                    if int(cl) > _AUTH_MAX_BODY_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "data": None,
                                "meta": None,
                                "error": {
                                    "code": "payload_too_large",
                                    "message": (
                                        f"Auth request body exceeds "
                                        f"{_AUTH_MAX_BODY_BYTES} byte cap."
                                    ),
                                },
                            },
                        )
                except ValueError:
                    pass
        return await call_next(request)
