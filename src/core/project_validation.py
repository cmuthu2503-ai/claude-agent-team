"""Validation helpers for project fields (PM-06).

All raise ValueError on invalid input — the API layer catches and converts
to 400 responses with a field-level error message.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from src.models.base import (
    PROJECT_COLOR_PALETTE,
    PROJECT_ICON_SET,
    UNASSIGNED_PROJECT_ID,
)

NAME_MAX_LEN = 80
DESC_MAX_LEN = 500
TAG_MAX_LEN = 25
TAGS_MAX_COUNT = 10
REPO_URL_MAX_LEN = 300

# Lower-cased tag charset — keep this conservative for now. Letters, digits,
# hyphens, underscores, spaces. No commas or slashes (they confuse tag UIs).
_TAG_OK = re.compile(r"^[a-z0-9 _\-]+$")


def validate_name(name: str) -> str:
    """Trim, reject empty / too long. Returns the cleaned name."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Project name is required.")
    if len(cleaned) > NAME_MAX_LEN:
        raise ValueError(f"Project name must be ≤{NAME_MAX_LEN} characters (got {len(cleaned)}).")
    return cleaned


def validate_description(description: str | None) -> str:
    cleaned = (description or "").strip()
    if len(cleaned) > DESC_MAX_LEN:
        raise ValueError(f"Description must be ≤{DESC_MAX_LEN} characters (got {len(cleaned)}).")
    return cleaned


def validate_color(color: str | None) -> str:
    """PRJ-009 — must be one of the 8 preset hex codes."""
    if not color:
        return "#00f0ff"
    if color not in PROJECT_COLOR_PALETTE:
        raise ValueError(
            f"Color {color!r} is not in the preset palette. "
            f"Allowed: {', '.join(PROJECT_COLOR_PALETTE)}"
        )
    return color


def validate_icon(icon: str | None) -> str:
    """PRJ-010 — must be one of the 8 preset lucide icon names."""
    if not icon:
        return "folder"
    if icon not in PROJECT_ICON_SET:
        raise ValueError(
            f"Icon {icon!r} is not in the preset set. "
            f"Allowed: {', '.join(PROJECT_ICON_SET)}"
        )
    return icon


def validate_tags(tags: list[str] | None) -> list[str]:
    """PRJ-011 — lowercase, strip, dedupe, ≤10 entries / ≤25 chars each."""
    if not tags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            raise ValueError(f"Tag {raw!r} must be a string.")
        t = raw.strip().lower()
        if not t:
            continue
        if len(t) > TAG_MAX_LEN:
            raise ValueError(f"Tag {t!r} exceeds {TAG_MAX_LEN} characters.")
        if not _TAG_OK.match(t):
            raise ValueError(
                f"Tag {t!r} contains invalid characters. "
                "Allowed: letters, digits, spaces, hyphens, underscores."
            )
        if t in seen:
            continue  # dedup silently — matches typical tag-input UX
        seen.add(t)
        cleaned.append(t)
    if len(cleaned) > TAGS_MAX_COUNT:
        raise ValueError(f"At most {TAGS_MAX_COUNT} tags allowed (got {len(cleaned)}).")
    return cleaned


def validate_repo_url(url: str | None) -> str:
    """PRJ-013 — optional URL, https-only, well-formed."""
    if not url:
        return ""
    url = url.strip()
    if len(url) > REPO_URL_MAX_LEN:
        raise ValueError(f"Repo URL exceeds {REPO_URL_MAX_LEN} characters.")
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"Repo URL is malformed: {url!r}")
    if parsed.scheme not in ("https", "http"):
        raise ValueError("Repo URL must start with https:// (http:// also accepted).")
    if not parsed.netloc:
        raise ValueError("Repo URL is missing a host.")
    return url


def validate_target_date(dt: datetime | str | None) -> datetime | None:
    """PRJ-014 — optional. If provided, must be today or in the future on create.
    (Edit can leave a past date in place — that's how 'Overdue' renders.)"""
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            raise ValueError(f"target_date is not a valid ISO date: {dt!r}")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt < today:
        raise ValueError("target_date must be today or in the future.")
    return dt


def assert_not_unassigned(project_id: str, action: str) -> None:
    """PRJ-008 / PM-14 — the seeded Unassigned project is immutable.
    Use at the start of update / delete / archive handlers."""
    if project_id == UNASSIGNED_PROJECT_ID:
        raise ValueError(
            f"The Unassigned project cannot be {action}. "
            "It is the system catch-all for legacy or orphaned requests."
        )
