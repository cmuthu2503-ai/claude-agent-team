"""Project template loader (PM-08).

Reads config/project_templates.yaml at backend startup and exposes it as
a simple in-memory cache. The cache is read-only at runtime — to add a
new template, edit the YAML and restart the backend.

The structure is intentionally minimal: a list of {id, name, description,
starter_checklist[]}. The starter_checklist items render as a clickable
"Next Steps" panel on the project detail page (PRJ-017); they do NOT
auto-submit requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ChecklistItem:
    description: str
    task_type: str
    priority: str


@dataclass(frozen=True)
class ProjectTemplate:
    id: str
    name: str
    description: str
    starter_checklist: list[ChecklistItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "starter_checklist": [
                {
                    "description": item.description,
                    "task_type": item.task_type,
                    "priority": item.priority,
                }
                for item in self.starter_checklist
            ],
        }


_TEMPLATES_PATH = Path("config/project_templates.yaml")
_CACHE: dict[str, ProjectTemplate] | None = None


def _load(path: Path = _TEMPLATES_PATH) -> dict[str, ProjectTemplate]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, ProjectTemplate] = {}
    for entry in raw.get("templates", []):
        items = [
            ChecklistItem(
                description=item["description"],
                task_type=item["task_type"],
                priority=item["priority"],
            )
            for item in entry.get("starter_checklist") or []
        ]
        tpl = ProjectTemplate(
            id=entry["id"],
            name=entry["name"],
            description=entry.get("description", ""),
            starter_checklist=items,
        )
        out[tpl.id] = tpl
    return out


def load_templates(path: Path | None = None) -> dict[str, ProjectTemplate]:
    """Read templates from disk. Call once at startup; subsequent calls return
    the cached result."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _load(path or _TEMPLATES_PATH)
    return _CACHE


def get_template(template_id: str | None) -> ProjectTemplate | None:
    """Look up a single template by id. None if the id is unknown or null."""
    if not template_id:
        return None
    return load_templates().get(template_id)


def all_templates() -> list[ProjectTemplate]:
    """Stable-ordered list (YAML order preserved by Python ≥3.7 dict)."""
    return list(load_templates().values())


def reset_cache() -> None:
    """For tests — force the next call to re-read from disk."""
    global _CACHE
    _CACHE = None
