"""SSE-safe headers middleware — REQ-005.

PRD §10.4 + API Spec §2.12 require that Server-Sent Events responses
carry headers that defeat both HTTP caches and proxy buffering. We set
them centrally for any path containing ``/events`` so individual route
handlers don't have to remember.

Why this works for ``StreamingResponse``:
    Starlette's BaseHTTPMiddleware returns the *response object* before
    the stream body is sent to the client. Mutating ``response.headers``
    at that point modifies the headers dict that Starlette uses to build
    the outgoing HTTP response start message — the headers are flushed
    BEFORE any event-data bytes, which is exactly what SSE proxies
    inspect to decide whether to buffer.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SSEHeadersMiddleware(BaseHTTPMiddleware):
    """Tag any response whose request path contains ``/events`` with the
    two headers that tell HTTP caches and nginx-style proxies to stream
    bytes through immediately:

      - ``Cache-Control: no-cache, no-transform``
      - ``X-Accel-Buffering: no``  (nginx-specific, harmless elsewhere)

    Edge case: the substring match means ``/api/v1/eventstream-config``
    would also get the headers. That's documented as accepted behaviour —
    the headers are harmless on JSON responses and the alternative
    (path-prefix regex) adds complexity for a hypothetical clash.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response = await call_next(request)
        if "/events" in request.url.path:
            response.headers["Cache-Control"] = "no-cache, no-transform"
            response.headers["X-Accel-Buffering"] = "no"
        return response
