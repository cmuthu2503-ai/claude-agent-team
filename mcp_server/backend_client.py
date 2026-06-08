"""Thin REST adapter: agent-team-mcp → Agent Team backend (HAI-07).

The MCP server holds NO business logic (FR-003). Every tool ultimately calls the
existing ``/api/v1`` REST API through this client, authenticating with the
long-lived service token (FR-004). On failure it raises ``BackendError`` with a
human-readable, recursively root-caused message so MCP tools can relay something
actionable to Hermes instead of an opaque stack trace (FR-007).
"""

from __future__ import annotations

from typing import Any

import httpx

try:  # support both package and flat (container) layouts
    from mcp_server.config import settings
except ModuleNotFoundError:  # pragma: no cover - container runs flat
    from config import settings  # type: ignore[no-redef]


class BackendError(Exception):
    """A clean, human-readable failure from a backend call."""


def root_cause(exc: BaseException) -> str:
    """Walk the ``__cause__`` / ``__context__`` chain to the deepest exception
    and render it as ``Type: message`` — so a wrapped httpx error surfaces the
    real reason (e.g. ConnectionRefused) instead of the outer wrapper."""
    seen: set[int] = set()
    cur: BaseException = exc
    while id(cur) not in seen:
        seen.add(id(cur))
        nxt = cur.__cause__ or cur.__context__
        if nxt is None:
            break
        cur = nxt
    return f"{type(cur).__name__}: {cur}"


def extract_error_message(body: Any) -> str | None:
    """Pull a human message out of a backend error body. Handles both the
    platform envelope (``{"error": {"message": ...}}`` / ``{"error": "..."}``)
    and FastAPI's ``{"detail": ...}``. Recurses into nested ``error`` objects."""
    if body is None:
        return None
    if isinstance(body, str):
        return body or None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return (
                err.get("message")
                or err.get("detail")
                or extract_error_message(err.get("error"))
            )
        if isinstance(err, str) and err:
            return err
        detail = body.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        if detail is not None:
            return str(detail)
    return None


class BackendClient:
    """Async HTTP client bound to the backend base URL + service token.

    Unwraps the platform's ``{data, meta, error}`` envelope on success (returns
    ``data``); raises ``BackendError`` on transport failure or non-2xx.
    """

    def __init__(
        self,
        base_url: str | None = None,
        service_token: str | None = None,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = (base_url or settings.BACKEND_URL).rstrip("/")
        token = service_token if service_token is not None else settings.SERVICE_TOKEN
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout
        self._transport = transport  # injectable for tests (httpx.MockTransport)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                resp = await client.request(
                    method, url, params=params, json=json, headers=self._headers
                )
            except httpx.HTTPError as e:
                raise BackendError(
                    f"Cannot reach Agent Team backend ({method} {url}): {root_cause(e)}"
                ) from e
        return self._handle(resp, method, url)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, json: Any | None = None) -> Any:
        return await self.request("POST", path, json=json)

    @staticmethod
    def _handle(resp: httpx.Response, method: str, url: str) -> Any:
        try:
            body: Any = resp.json()
        except ValueError:
            body = None

        if resp.is_success:
            # Unwrap the {data, meta, error} envelope when present.
            if isinstance(body, dict) and "data" in body:
                return body["data"]
            return body

        msg = extract_error_message(body) or (resp.text or f"HTTP {resp.status_code}")
        raise BackendError(f"Backend {method} {url} failed ({resp.status_code}): {msg}")
