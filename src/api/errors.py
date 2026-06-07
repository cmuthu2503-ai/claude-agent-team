"""RFC 7807 problem+json error handling (REQ-005 / REQ-009).

Defines:
  * ProblemDetails  - Pydantic v2 model matching RFC 7807 with an extra
                      `request_id` member for trace correlation.
                      extra='allow' so handlers may attach extension
                      fields (e.g. `errors`, `checks`).
  * NotFoundError   - domain 404
  * ConflictError   - domain 409
  * register_exception_handlers(app) - wires handlers for
    RequestValidationError, HTTPException, NotFoundError, ConflictError,
    and the catch-all Exception.

All handlers return `application/problem+json` so the envelope middleware
recognises them and passes the body through unmodified.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

PROBLEM_JSON_MEDIA_TYPE = "application/problem+json"

# Keys that are core ProblemDetails fields - must NOT be present in the
# `extras` dict when we splat it, or we'd collide with the explicit kwargs.
_RESERVED_PROBLEM_KEYS = frozenset(
    {"type", "title", "status", "detail", "instance", "request_id"}
)


class ProblemDetails(BaseModel):
    """RFC 7807 problem detail object + request_id trace field."""

    model_config = ConfigDict(extra="allow")

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str
    request_id: str


class NotFoundError(Exception):
    """Domain-level 404 - resource not found."""

    def __init__(self, detail: str = "Not Found") -> None:
        super().__init__(detail)
        self.detail = detail


class ConflictError(Exception):
    """Domain-level 409 - state conflict (e.g. duplicate, version mismatch)."""

    def __init__(self, detail: str = "Conflict") -> None:
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------- helpers

def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _problem_response(problem: ProblemDetails) -> JSONResponse:
    return JSONResponse(
        content=problem.model_dump(),
        status_code=problem.status,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
    )


def _safe_extras(extras: dict[str, Any]) -> dict[str, Any]:
    """Drop keys that would collide with explicit ProblemDetails kwargs."""
    return {
        k: v for k, v in extras.items() if k not in _RESERVED_PROBLEM_KEYS
    }


_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    410: "Gone",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def _status_title(status: int) -> str:
    return _STATUS_TITLES.get(status, "Error")


# ---------------------------------------------------------------- handlers

def register_exception_handlers(app: FastAPI) -> None:
    """Register RFC 7807 handlers on the FastAPI app.

    FastAPI/Starlette dispatch by exception class, so specific handlers
    (HTTPException, ConflictError, etc.) still win for their own types
    even though the broad Exception handler is registered too.
    """

    @app.exception_handler(RequestValidationError)
    async def _on_validation(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Strip non-JSON-serialisable bits (e.g. bytes in `input`) from
        # each error so model_dump downstream stays JSON-safe.
        errors: list[dict[str, Any]] = []
        for err in exc.errors():
            cleaned = {k: v for k, v in err.items() if k != "ctx"}
            errors.append(cleaned)
        problem = ProblemDetails(
            type="about:blank",
            title="Unprocessable Entity",
            status=422,
            detail="Request validation failed",
            instance=request.url.path,
            request_id=_request_id(request),
            errors=errors,
        )
        return _problem_response(problem)

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        title = _status_title(exc.status_code)
        extras: dict[str, Any] = {}
        detail_text: str | None
        if isinstance(exc.detail, dict):
            # Promote dict keys to extension fields; pull out `detail`
            # specially so it lands in the standard RFC 7807 slot.
            raw = dict(exc.detail)
            detail_text = raw.pop("detail", None) or title
            extras = _safe_extras(raw)
        else:
            detail_text = (
                str(exc.detail) if exc.detail is not None else None
            )

        problem = ProblemDetails(
            type="about:blank",
            title=title,
            status=exc.status_code,
            detail=detail_text,
            instance=request.url.path,
            request_id=_request_id(request),
            **extras,
        )
        return _problem_response(problem)

    @app.exception_handler(NotFoundError)
    async def _on_not_found(
        request: Request,
        exc: NotFoundError,
    ) -> JSONResponse:
        problem = ProblemDetails(
            type="about:blank",
            title="Not Found",
            status=404,
            detail=exc.detail,
            instance=request.url.path,
            request_id=_request_id(request),
        )
        return _problem_response(problem)

    @app.exception_handler(ConflictError)
    async def _on_conflict(
        request: Request,
        exc: ConflictError,
    ) -> JSONResponse:
        problem = ProblemDetails(
            type="about:blank",
            title="Conflict",
            status=409,
            detail=exc.detail,
            instance=request.url.path,
            request_id=_request_id(request),
        )
        return _problem_response(problem)

    @app.exception_handler(Exception)
    async def _on_unhandled(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        # Log full traceback server-side; do NOT leak exc.args to clients.
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": _request_id(request),
                "path": request.url.path,
                "exc_type": type(exc).__name__,
            },
        )
        problem = ProblemDetails(
            type="about:blank",
            title="Internal Server Error",
            status=500,
            detail=type(exc).__name__,
            instance=request.url.path,
            request_id=_request_id(request),
        )
        return _problem_response(problem)
