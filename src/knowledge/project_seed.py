"""KB-19 — one-time seeding of an existing app's docs into its project KB.

For the *enhance-existing* flow: when a Project already has a local source tree
(its docs/specs/readmes on disk), seed that knowledge into the project's
isolated ``kb_project_<id>`` namespace at provisioning time so the agents start
grounded instead of cold.

``seed_project_corpus`` walks a root for text docs (markdown / text / readmes —
not binary or full source dumps, which add noise more than grounding value),
ingests them via the standard pipeline (hash-dedup, so re-runs are no-ops), and
auto-approves them (the existing repo is trusted, like the platform corpus).
Capped + soft-failing per file so a big or messy tree can't stall provisioning.

When the root doesn't exist or has no matching docs (a brand-new project), it's
a clean no-op — the cold-start path (KB-19, base.py) then labels the app KB as
sparse so agents lean on the PRD/brief and flag ungrounded claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# Text doc patterns worth grounding in. Deliberately NOT a full code dump.
DEFAULT_SEED_GLOBS: tuple[str, ...] = (
    "*.md", "*.txt", "README*", "docs/**/*.md", "docs/**/*.txt",
)
SEED_MAX_FILES = 60
SEED_MAX_BYTES = 300_000
# Directories never worth walking.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".next"}


@dataclass
class SeedSummary:
    project_id: str
    namespace: str
    files_seen: int = 0
    ingested: int = 0
    skipped: int = 0
    chunks: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id, "namespace": self.namespace,
            "files_seen": self.files_seen, "ingested": self.ingested,
            "skipped": self.skipped, "chunks": self.chunks, "failures": self.failures,
        }


def _collect(root: Path, include_globs: tuple[str, ...]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in include_globs:
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            seen[p] = None
        if len(seen) >= SEED_MAX_FILES:
            break
    return list(seen)[:SEED_MAX_FILES]


async def seed_project_corpus(
    *,
    pipeline: Any,
    store: Any,
    project_id: str,
    namespace: str,
    root: str | Path,
    include_globs: tuple[str, ...] | None = None,
    auto_approve: bool = True,
) -> SeedSummary:
    """Ingest an existing app's text docs from ``root`` into its project KB.
    Idempotent + per-file soft-fail. Returns a summary; never raises."""
    root = Path(root)
    summary = SeedSummary(project_id=project_id, namespace=namespace)
    if not root.is_dir():
        return summary
    bucket = await store.provision_project(project_id, namespace)
    files = _collect(root, include_globs or DEFAULT_SEED_GLOBS)
    summary.files_seen = len(files)
    for path in files:
        rel = str(path.relative_to(root))
        try:
            if path.stat().st_size > SEED_MAX_BYTES:
                summary.skipped += 1
                continue
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            try:
                text = path.read_text(encoding="latin-1")
            except Exception as e:  # noqa: BLE001
                summary.failures.append(f"{rel}: {e}")
                continue
        try:
            res = await pipeline.ingest_text(
                text=text, title=path.name, source_type="repo_doc",
                namespace=namespace, bucket_ids=[bucket.bucket_id],
                uri=rel, project_id=project_id,
            )
            if res.skipped:
                summary.skipped += 1
            else:
                summary.ingested += 1
                summary.chunks += res.chunks
                if auto_approve:
                    await store.set_document_status(
                        res.doc_id, "approved", curated_by="seed")
        except Exception as e:  # noqa: BLE001
            summary.failures.append(f"{rel}: {e}")
    logger.info(
        "kb_project_seeded", project_id=project_id, namespace=namespace,
        files=summary.files_seen, ingested=summary.ingested,
        skipped=summary.skipped, chunks=summary.chunks, failures=len(summary.failures),
    )
    return summary
