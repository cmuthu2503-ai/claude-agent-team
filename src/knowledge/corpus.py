"""Platform corpus discovery (KB-06).

Lists the platform's own knowledge files to ingest into the system
"Platform" bucket — the team's accumulated architecture, conventions,
lessons, and designs. Resilient: missing files are skipped, not errors
(e.g. ``CLAUDE.md`` isn't always mounted in every environment).

Default selection (override via ``include_globs``):
  - ``CLAUDE.md``                — conventions / agent operating guide
  - ``docs/*.md``               — architecture, cross-cutting, PRDs, designs,
                                   playbooks, and ``agent-lessons-learned.md``
  - ``docs/research/**/summary.md`` — concise research executive summaries

Generated binaries (pptx/pdf), node_modules, and the mockups are excluded —
only human-readable markdown/text that grounds agent work.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_INCLUDE_GLOBS: tuple[str, ...] = (
    "CLAUDE.md",
    "docs/*.md",
    "docs/research/**/summary.md",
)

# Never ingest these even if a glob would match.
_EXCLUDE_SUBSTR = ("node_modules", "/mockups/", ".pending.md")


def platform_corpus_files(
    root: str | Path,
    include_globs: tuple[str, ...] | list[str] | None = None,
) -> list[Path]:
    """Resolve the corpus file list under ``root`` (the repo root, ``/app``
    in-container). De-duped, sorted, existing-only."""
    base = Path(root)
    globs = tuple(include_globs) if include_globs else DEFAULT_INCLUDE_GLOBS
    seen: dict[Path, None] = {}
    for g in globs:
        # A bare filename (no glob chars) is a direct path check.
        if any(ch in g for ch in "*?[") is False:
            p = base / g
            if p.is_file():
                seen.setdefault(p.resolve(), None)
            continue
        for p in base.glob(g):
            if not p.is_file():
                continue
            sp = str(p).replace("\\", "/")
            if any(x in sp for x in _EXCLUDE_SUBSTR):
                continue
            seen.setdefault(p.resolve(), None)
    return sorted(seen.keys())


def relative_uri(root: str | Path, path: Path) -> str:
    """A stable citation pointer (repo-relative posix path)."""
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.name
