"""Governed-mode resolution (HAI-45 / FR-063, NFR-001).

"Governed mode" decides whether the platform's AUTONOMOUS loops (auto-dispatch,
auto-rollback, …) act on their own (legacy) or route through the approval gate as
proposals a human confirms. The whole Hermes integration is opt-in by this switch,
so a deployment with no Hermes identity behaves exactly as it did pre-integration.

Resolution (precedence):
  1. Explicit ``GOVERNED_MODE`` env (true/false) — operator override, always wins.
  2. Otherwise IDENTITY-AWARE default: governed iff at least one live service-token
     (a Hermes operator) exists. No Hermes identity → legacy autonomy (NFR-001).

Fail-safe: any error resolving the default falls back to LEGACY (autonomy), so a
storage hiccup can't silently freeze the platform's existing automation.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

_FALSEY = {"false", "0", "no", "off"}
_TRUTHY = {"true", "1", "yes", "on"}


def _parse_env(env_value: str | None) -> bool | None:
    if env_value is None:
        return None
    v = env_value.strip().lower()
    if v == "":
        return None
    if v in _FALSEY:
        return False
    if v in _TRUTHY:
        return True
    # Unrecognized non-empty value → treat as explicit-on (operator clearly set it).
    return True


async def resolve_governed_mode(state: Any, env_value: str | None) -> bool:
    """Resolve the effective governed-mode flag. ``env_value`` is typically
    ``os.getenv("GOVERNED_MODE")``."""
    explicit = _parse_env(env_value)
    if explicit is not None:
        logger.info("governed_mode_resolved", source="env", governed=explicit)
        return explicit

    try:
        tokens = await state.list_service_tokens()
        governed = any(not t.is_revoked for t in tokens)
    except Exception as e:  # noqa: BLE001 — never freeze automation on a lookup error
        logger.warning("governed_mode_default_failed", error=str(e), fallback="legacy")
        return False

    logger.info(
        "governed_mode_resolved",
        source="identity",
        governed=governed,
        hint="a live Hermes service token enables governance" if not governed else None,
    )
    return governed
