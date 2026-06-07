"""Response envelope middleware (REQ-003 / REQ-004).

Wraps successful JSON responses in `{"data": ..., "meta": {...}, "error": null}`
so the frontend can parse every response the same way. Deliberately skips:

  * 204 No Content
  * StreamingResponse instances (we cannot drain SSE without breaking it)
  * Content-Type: text/event-stream  (defensive — SSE)
  * Content-Type: application/problem+json  (errors stay raw per RFC 7807)
  * Any non-application/json content type (file downloads, plain text, etc.)
"""

from __future__ import annotations

import json
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import ASGIApp


class EnvelopeMiddleware(BaseHTTPMiddleware):
    """Wrap JSON response bodies in the standard {data, meta, error} envelope."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response: Response = await call_next(request)

        if self._should_skip(response):
            return response

        # Drain the streaming body emitted by BaseHTTPMiddleware.
        body_bytes = b""
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            if isinstance(chunk, str):
                body_bytes += chunk.encode("utf-8")
            else:
                body_bytes += chunk

        # Empty body → nothing to wrap; return a 1:1 reconstruction.
        if not body_bytes:
            return Response(
                content=b"",
                status_code=response.status_code,
                headers=self._forward_headers(response.headers),
                media_type=response.media_type,
            )

        try:
            parsed: Any = json.loads(body_bytes)
        except json.JSONDecodeError:
            # Content-type claimed JSON but body wasn't parseable — pass
            # through rather than corrupt the response.
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=self._forward_headers(response.headers),
                media_type=response.media_type,
            )

        request_id = getattr(request.state, "request_id", "unknown")
        envelope = {
            "data": parsed,
            "meta": {"request_id": request_id},
            "error": None,
        }

        return JSONResponse(
            content=envelope,
            status_code=response.status_code,
            headers=self._forward_headers(response.headers),
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _should_skip(response: Response) -> bool:
        if response.status_code == 204:
            return True
        if isinstance(response, StreamingResponse):
            return True
        content_type = response.headers.get("content-type", "").lower()
        if content_type.startswith("text/event-stream"):
            return True
        if content_type.startswith("application/problem+json"):
            return True
        if not content_type.startswith("application/json"):
            return True
        return False

    @staticmethod
    def _forward_headers(src) -> dict[str, str]:
        """Copy outbound headers, dropping content-length and content-type.

        JSONResponse sets its own content-length and content-type
        (application/json) — forwarding the originals would duplicate or
        conflict with those, so we strip them here.
        """
        skip = {"content-length", "content-type"}
        out: dict[str, str] = {}
        for key, value in src.items():
            if key.lower() in skip:
                continue
            out[key] = value
        return out
