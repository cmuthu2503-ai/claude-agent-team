"""Request-ID middleware (REQ-002).

Generates a UUIDv4 X-Request-ID per request, or reuses the client-supplied
one if it parses as a valid UUID. The ID is stored on `request.state` so
route handlers and downstream middleware (envelope, error handlers) can
read it, and is echoed on the response header so clients can correlate.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a UUIDv4 X-Request-ID to every request/response."""

    HEADER = "X-Request-ID"

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        incoming = request.headers.get(self.HEADER)
        request_id = self._coerce_uuid(incoming)
        request.state.request_id = request_id

        response: Response = await call_next(request)
        response.headers[self.HEADER] = request_id
        return response

    @staticmethod
    def _coerce_uuid(value: str | None) -> str:
        """Return `value` if it parses as a UUID, else a fresh UUIDv4 string."""
        if value:
            try:
                # `uuid.UUID` accepts any valid UUID (v1-v5); spec says we
                # mint v4 on miss, but we accept any well-formed UUID the
                # client supplies for max interop.
                return str(uuid.UUID(value))
            except (ValueError, AttributeError, TypeError):
                pass
        return str(uuid.uuid4())
