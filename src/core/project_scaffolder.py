"""Project scaffolder — materializes a per-project working tree from a
template at project creation time.

Templates live under ``config/project-templates/<kind>/`` in the platform
repo. Each template is a plain directory tree containing files with
``{{KEY}}`` placeholders. The scaffolder walks the tree, substitutes
placeholders, and writes the resulting files into the project's host
directory at ``C:/ai-projects/<ProjectName>/`` (via the
``/host/ai-projects`` bind mount inside Docker).

Why a scaffold at creation time (not first deploy):
  - The user sees a working app the moment they create the project.
  - Allocated ports are baked into the scaffolded ``docker-compose.yml``
    once, atomically, so there's no "next free port" lookup race at
    deploy time.
  - Agent emissions layer on top via whole-file replacement — the
    agent isn't responsible for bootstrapping the toolchain.

Substitution model:
  - Pure text replace of ``{{KEY}}`` tokens. No nested expressions, no
    conditionals, no escaping. KISS — these are starter files, not
    Jinja templates. If we need conditional logic later we can swap in
    Jinja without changing the on-disk template format (just move
    delimiter syntax to ``{% %}``).
  - Binary files would corrupt under UTF-8 decode + replace. Defensive
    fallback: copy unchanged if the file isn't valid UTF-8.

Idempotency:
  - If a target file already exists, the scaffolder **skips** it. This
    way a re-scaffold (e.g. after a partial failure) won't clobber agent
    work that has already landed.
  - To force a full re-scaffold, the caller should clear the target
    directory first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# Set of project "kinds" the scaffolder knows about. The string values
# are also the subdirectory names under ``config/project-templates/``.
KNOWN_KINDS: tuple[str, ...] = ("web-app", "api-service", "frontend-app")


@dataclass
class ScaffoldResult:
    """Returned to the caller so the API layer can include scaffold
    details in the create-project response and the GitHub publisher
    can commit the same file list to the new repo."""

    ok: bool
    kind: str
    project_root: str
    files_written: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "project_root": self.project_root,
            "files_written": self.files_written,
            "files_skipped": self.files_skipped,
            "error": self.error,
        }


class ProjectScaffolder:
    """Stateful only in that it caches the templates directory location.
    Safe to share across requests; no mutable state. Construct once at
    app startup or per-call — doesn't matter."""

    def __init__(self, templates_dir: Path | str = "config/project-templates") -> None:
        self.templates_dir = Path(templates_dir)
        if not self.templates_dir.exists():
            # Don't raise — the path might not exist in unit-test
            # contexts and we want construction to remain cheap. Errors
            # surface on the first ``scaffold()`` call.
            logger.warning(
                "scaffolder.templates_dir_missing path=%s", self.templates_dir,
            )

    def scaffold(
        self,
        *,
        kind: str,
        project_root: Path,
        substitutions: dict[str, str],
    ) -> ScaffoldResult:
        """Materialize the template for ``kind`` into ``project_root``,
        substituting ``{{KEY}}`` tokens from ``substitutions``.

        Args:
            kind: One of ``KNOWN_KINDS``. Selects which template directory
                  under ``self.templates_dir`` to use.
            project_root: Destination directory (created if missing).
                          Caller is responsible for ensuring this is a
                          safe path — see ``project_workspace.project_root_dir``
                          which routes through ``validate_name``.
            substitutions: Token map. Required keys depend on the
                           template, but conventionally:
                             - ``PROJECT_NAME``  (verbatim, e.g. "CrewAIAgentTeam")
                             - ``PROJECT_SLUG``  (kebab-case, GitHub-safe)
                             - ``BACKEND_PORT``  (str, e.g. "8100")
                             - ``FRONTEND_PORT`` (str, e.g. "3100")

        Returns:
            ScaffoldResult — soft-fail: ``ok=False`` on errors with
            ``error`` populated, rather than raising. The caller should
            decide whether a partial scaffold is recoverable.
        """
        if kind not in KNOWN_KINDS:
            return ScaffoldResult(
                ok=False, kind=kind, project_root=str(project_root),
                error=f"Unknown project kind {kind!r}. Known: {', '.join(KNOWN_KINDS)}",
            )

        template_root = self.templates_dir / kind
        if not template_root.exists() or not template_root.is_dir():
            return ScaffoldResult(
                ok=False, kind=kind, project_root=str(project_root),
                error=f"Template directory missing: {template_root}",
            )

        try:
            project_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ScaffoldResult(
                ok=False, kind=kind, project_root=str(project_root),
                error=f"Could not create project root: {e}",
            )

        written: list[str] = []
        skipped: list[str] = []

        for src_path in _iter_template_files(template_root):
            rel = src_path.relative_to(template_root)
            dest_path = project_root / rel

            if dest_path.exists():
                # Idempotency: don't clobber existing files. Lets a
                # re-scaffold (after a partial failure) preserve agent
                # edits or hand-tuned local changes.
                skipped.append(str(rel).replace("\\", "/"))
                continue

            try:
                content = _read_and_substitute(src_path, substitutions)
            except Exception as e:
                logger.warning(
                    "scaffolder.read_failed src=%s err=%s", src_path, e,
                )
                return ScaffoldResult(
                    ok=False, kind=kind, project_root=str(project_root),
                    files_written=written, files_skipped=skipped,
                    error=f"Failed to read template file {rel}: {e}",
                )

            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    dest_path.write_text(content, encoding="utf-8")
                else:
                    dest_path.write_bytes(content)
            except OSError as e:
                logger.warning(
                    "scaffolder.write_failed dest=%s err=%s", dest_path, e,
                )
                return ScaffoldResult(
                    ok=False, kind=kind, project_root=str(project_root),
                    files_written=written, files_skipped=skipped,
                    error=f"Failed to write {rel}: {e}",
                )

            written.append(str(rel).replace("\\", "/"))

        logger.info(
            "scaffolder.ok kind=%s root=%s written=%d skipped=%d",
            kind, project_root, len(written), len(skipped),
        )
        return ScaffoldResult(
            ok=True, kind=kind, project_root=str(project_root),
            files_written=written, files_skipped=skipped,
        )

    def render_template_files(
        self,
        *,
        kind: str,
        substitutions: dict[str, str],
    ) -> dict[str, str | bytes]:
        """In-memory render — return a dict of ``rel_path → content``
        WITHOUT writing to disk. Used to build the initial GitHub
        commit payload from the same template the disk write used,
        guaranteeing local-disk and remote-repo land identical content
        in lockstep.

        Returns an empty dict on bad kind. Raises on read errors —
        unlike ``scaffold()``, this path is consumed by the API
        request flow where a hard failure is appropriate.
        """
        if kind not in KNOWN_KINDS:
            return {}
        template_root = self.templates_dir / kind
        if not template_root.exists():
            return {}
        out: dict[str, str | bytes] = {}
        for src_path in _iter_template_files(template_root):
            rel = str(src_path.relative_to(template_root)).replace("\\", "/")
            out[rel] = _read_and_substitute(src_path, substitutions)
        return out


def _iter_template_files(template_root: Path) -> Iterable[Path]:
    """Walk the template directory yielding regular files only.
    Skips anything that isn't a file (symlinks, sockets, etc.)."""
    for p in sorted(template_root.rglob("*")):
        if p.is_file():
            yield p


def _read_and_substitute(
    src_path: Path,
    substitutions: dict[str, str],
) -> str | bytes:
    """Read a template file, do ``{{KEY}}`` replacement, return content.

    Returns ``str`` for text files (UTF-8 decoded + substituted) or
    ``bytes`` if the file isn't valid UTF-8 (then no substitution is
    performed — caller writes raw). None of our v1 templates have
    binary files, but this defends against someone adding one.
    """
    raw = src_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    for key, value in substitutions.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text
