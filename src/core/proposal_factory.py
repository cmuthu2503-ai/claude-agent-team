"""Single source of truth for proposal construction (HAI-63).

Both the HTTP create route (HAI-23) and the in-process gate (HAI-63) need to build
a Proposal the same way — same id scheme, same one-time approval-token generation
(hash stored, raw returned for the human-facing event). Centralizing it here means
the security-critical token handling lives in exactly one place.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from src.auth.service import hash_service_token
from src.models.base import Proposal

_DEFAULT_TTL_SECONDS = 86400  # 24h


def new_proposal(
    *,
    action_type: str,
    proposed_by: str,
    target_ref: str | None = None,
    payload: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[Proposal, str]:
    """Build an unsaved PENDING ``Proposal`` plus its RAW one-time approval token.

    Only the token's hash is stored on the proposal; the raw token is returned to
    the caller so it can ride the ``proposal.created`` event to the human (and is
    NEVER put in an API response or forwarded to the proposer — see HAI-30/61).
    """
    raw_token = secrets.token_urlsafe(32)
    proposal = Proposal(
        proposal_id=f"prop-{uuid.uuid4().hex[:12]}",
        action_type=action_type,
        target_ref=target_ref,
        payload=payload or {},
        proposed_by=proposed_by,
        ttl_seconds=ttl_seconds if (ttl_seconds and ttl_seconds > 0) else _DEFAULT_TTL_SECONDS,
        idempotency_key=idempotency_key,
        approval_token_hash=hash_service_token(raw_token),
    )
    return proposal, raw_token
