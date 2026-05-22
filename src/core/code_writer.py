"""Code Writer — parses agent code output, writes files, compiles, publishes to GitHub.

Compilation/test steps use ruff/tsc/pytest binaries that ARE installed in the
backend container. Git publishing uses the GitHub Trees API (no `git` CLI), so
this module works without git installed in the container.
"""

import asyncio
import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path

import structlog

from src.core.github_publisher import GitHubPublishError, GitHubPublisher
from src.models.base import DeploymentState, DeploymentStep
from src.state.base import StateStore

logger = structlog.get_logger()

PROJECT_ROOT = Path(".")

# Snapshot-and-validate guard (defense against patch-fragment / abbreviated
# file emissions). CodeWriter does whole-file replacement; if the agent emits
# a partial file, the rest gets silently deleted. These constants tune the
# heuristics. Tweak in tests via monkeypatch if you need different thresholds.
_MIN_LINES_FOR_DROP_CHECK = 20
"""Below this many existing lines, the percentage-drop check is skipped —
percentages are noisy for tiny files (5-line files dropping to 2 lines)."""

_MAX_LINE_DROP_PCT = 50.0
"""Reject any overwrite of an existing file >=20 lines if the new content
has fewer than this percent of the original line count remaining. The
case we're catching: 353-line themes.css → 85-line patch fragment = 76% drop."""

# Files that ARE expected to shrink as the agent canonicalises them.
# These are "config seeds" — the scaffold ships a starting version, but
# the agent has authority to refactor them into the modern shape (e.g.
# splitting a monolithic tsconfig.json into tsconfig.app.json +
# tsconfig.node.json with a 7-line root referencing both). The
# atomic-write drop guard would otherwise refuse the legitimate refactor.
#
# Match is on the file BASENAME (so it covers both top-level and
# subdirectory placements like `frontend/tsconfig.json`). Keep this list
# narrow — the guard is the only defence against patch-fragment emissions
# on real source files. Adding `*.py` or `*.tsx` here would defeat it.
#
# This list was added in response to REQ-F86080, where the frontend
# scaffold's 21-line legacy tsconfig.json was being replaced by the
# 7-line modern project-references shape and the guard kept rejecting
# the commit until MAX_REWORK_CYCLES was exhausted.
_DROP_GUARD_EXEMPT_BASENAMES: frozenset[str] = frozenset({
    # TypeScript project-references pattern — root tsconfig.json shrinks
    # to ~7 lines when the agent splits compiler options into siblings.
    "tsconfig.json",
    # Frontend infra config: agents often replace the scaffold's
    # starter with a project-tailored version.
    "vite.config.ts",
    "vite.config.js",
    "tailwind.config.ts",
    "tailwind.config.js",
    "postcss.config.js",
    "postcss.config.cjs",
    "eslint.config.js",
    ".prettierrc",
    # CSS entry-point files. Scaffolds ship a verbose index.css with
    # baked-in theme tokens; agents are often instructed to delegate
    # theming to a sibling themes.css and reduce index.css to just
    # `@import './themes.css'; @tailwind base; ...`. That's a
    # legitimate refactor that the guard would otherwise reject.
    # (Killed REQ-F86080 retry attempt 2026-05-21.)
    "index.css",
    "main.css",
    # Dotfile configs frequently rewritten by the agent.
    ".gitignore",
    ".dockerignore",
    ".env.example",
    # Backend infra config.
    "pyproject.toml",
    "ruff.toml",
})
"""Basenames where the line-drop guard is intentionally skipped. The
marker-based guard (PATCH SCOPE, "rest of file unchanged", etc.) still
applies — those signal accidental fragments regardless of file type."""

_SUSPICIOUS_MARKERS: tuple[str, ...] = (
    # Explicit patch-style language from the agent
    "patch scope",
    "patch fragment",
    "splice these in-place",
    "splice in-place",
    "the patcher must",
    # Abbreviation markers — agent thinks the rest of the file is implied
    "rest of file unchanged",
    "rest of the file unchanged",
    "... existing code ...",
    "... unchanged ...",
    "// ... existing",
    "/* ... existing",
    "# ... existing",
)
"""Substrings that — if found in new file content for an EXISTING file —
indicate the agent emitted a fragment, not a full replacement. Compared
case-insensitively against the new content."""


# Paths agents are NEVER allowed to commit changes to unless the request's
# description explicitly names them. These are "off-limits by default" so
# agents can't silently modify their own configs, the dev orchestration
# files, or the CI scripts. Path checks are PREFIX-based (startswith).
_GUARDED_PATH_PREFIXES: tuple[str, ...] = (
    "config/agents/",   # Agent YAML — agents have been self-modifying these
    "config/tools.yaml",
    "config/workflows.yaml",
    "config/teams.yaml",
    "supervisor/",      # Deploy supervisor — system component, not agent-editable
    ".github/",         # CI / actions
    "Dockerfile",       # Docker build files
    "docker-compose",
)


class CodeWriteError(Exception):
    """Raised when code writing, compilation, or git push fails."""


class CodeWriter:
    """Parses code blocks from agent output, writes to disk, compiles, and publishes to GitHub.

    Publishing uses the GitHub Trees API via GitHubPublisher — no `git` CLI required.
    """

    def __init__(self, state: StateStore, project_root: str = ".") -> None:
        self.state = state
        self.root = Path(project_root)
        self.github = GitHubPublisher()
        # Drop-guard loop detection. Maps (request_id, file_path) →
        # sha256(content) of the LAST emission that the drop guard
        # rejected. Populated whenever the guard fires; consulted on
        # the next call so we can detect byte-identical re-emission
        # (the failure pattern from T-103e9025 / REQ-FEC71B where the
        # agent emitted the same 275-line shrink three cycles in a
        # row). Cleared per-request when materialize_files succeeds
        # so a fresh dispatch of the same task starts clean.
        self._recent_drop_rejections: dict[tuple[str, str], str] = {}

    async def commit_code(
        self,
        request_id: str,
        description: str,
        agent_outputs: dict[str, str],
        repo: str | None = None,
        project_root: Path | None = None,
        materialized_files: dict[str, str] | None = None,
    ) -> DeploymentState:
        """Full code commit pipeline: parse → write → compile → test → publish.

        Now factored into two halves so the workflow runner can split them
        across stages:
          - ``materialize_files()`` — Steps 1-6 (parse, write, guard, lint,
            test). Run RIGHT AFTER development stage so review/test see
            real on-disk content rather than scaffold + agent's pending
            text emissions.
          - ``_commit_to_github_only()`` — Steps 7-8 (publish + state).
            Run at the code_commit stage.

        When the workflow runner has already called materialize_files
        (the new path), it passes the result here as ``materialized_files``
        and we skip the materialize half. Without that arg (the legacy
        path: direct callers or workflows without a materialize stage)
        we run the full pipeline as before — backwards compatible.

        Args:
            request_id: The request ID this code belongs to
            description: Human-readable description for the commit message
            agent_outputs: Dict of agent_id → output_text (from backend/frontend specialists)
            repo: Target "owner/name" for the GitHub commit. None → falls back to
                  GITHUB_REPO env var (the platform's own repo, preserves
                  pre-WS behavior). The orchestrator resolves this per-request
                  by looking up the request's project (WS-09).
            project_root: Per-project working tree root (the
                  "per-project working tree" feature). When set, all
                  file ops (write, read, compile, test) happen under
                  THIS directory instead of ``self.root`` (the platform
                  tree). The orchestrator resolves this from the
                  request's project (when project_id is set) via
                  ``project_workspace.project_root_dir(project.name)``.
                  Legacy requests with no project still write to the
                  platform tree via ``self.root``.
            materialized_files: Optional pre-computed result from a prior
                  ``materialize_files()`` call. When provided, skip the
                  materialize half — files are already on disk and
                  validated. The new workflow runner sets this after
                  the development stage so review/test can see real
                  files; we then just commit to GitHub.

        Returns:
            DeploymentState with step = code_committed (ready for sidecar)

        Raises:
            CodeWriteError if any step fails
        """
        # New-path short-circuit: when the workflow runner already
        # materialized the files (by calling self.materialize_files
        # after the development stage), it passes the result here so
        # we skip the parse/write/lint/test work and just commit.
        if materialized_files is not None:
            return await self._commit_to_github_only(
                request_id=request_id,
                description=description,
                all_file_content=materialized_files,
                repo=repo,
            )

        # Legacy path: do the full pipeline in one go.
        all_file_content = await self.materialize_files(
            request_id=request_id,
            description=description,
            agent_outputs=agent_outputs,
            project_root=project_root,
        )
        return await self._commit_to_github_only(
            request_id=request_id,
            description=description,
            all_file_content=all_file_content,
            repo=repo,
        )

    async def materialize_files(
        self,
        request_id: str,
        description: str,
        agent_outputs: dict[str, str],
        project_root: Path | None = None,
    ) -> dict[str, str]:
        """Parse agent outputs, write files to disk, run lint + tests.

        This is the "pre-commit" half of the pipeline — extracted from
        ``commit_code`` so the workflow runner can call it RIGHT AFTER
        the development stage, BEFORE review/testing. That way the
        reviewer's ``file_read`` sees the agent's actual emission on
        disk rather than the scaffold's starter content.

        Before this split, ``### Full Source:`` blocks only got
        materialised at the code_commit stage (which runs after
        review/test), so the reviewer would file_read the scaffold,
        not the agent's work, and (incorrectly) report "phantom
        emission" on every cycle. The fix lets the reviewer see real
        files at review time — exactly the architecture the reviewer's
        prompt assumes.

        Steps performed (raises CodeWriteError on any failure — caller
        routes to rework):
          1a. Parse ``### Full Source:`` blocks from each agent's text
              output and write them to disk via the snapshot/validate
              guard (truncation + drop-guard + suspicious-marker checks).
          1b. Pick up files the agent edited via ``search_replace``
              tool calls (those are already on disk; we just read them
              back so they're included in the returned content map).
          2.  Reject any files under guarded prefixes the request didn't
              explicitly authorise (config/agents/**, etc.).
          3.  Compile Python via ``ruff check`` on the diff.
          4.  Compile TypeScript via the real prod ``npm run build``.
          5.  Run pytest on any test files the agent emitted.

        Returns the dict {rel_path: content} of materialised files so
        the caller (orchestrator's materialize handler) can stash it
        in artifacts for the later commit_code call to use directly.
        """
        # Resolve the effective root for THIS commit. The instance
        # attribute (`self.root`) stays at the platform tree so any
        # concurrent legacy request keeps working — only the per-call
        # binding switches. mkdir up-front: a project-scoped commit may
        # land in a freshly-created project tree (scaffold succeeded
        # but `docs/` is the only existing subdir).
        effective_root = project_root if project_root is not None else self.root
        try:
            effective_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise CodeWriteError(
                f"Could not access project root {effective_root}: {e}"
            ) from e
        # The set of guarded path prefixes that this specific request is
        # ALLOWED to touch — only those explicitly named in the description.
        # Everything else under _GUARDED_PATH_PREFIXES will be rejected.
        # This stops the agent self-modification pattern we saw in REQ-8C3B4F
        # (backend_specialist.yaml edited itself), REQ-D0742A (similar), and
        # REQ-D20A12 (test_config_validation.py touched out of scope).
        allowed_guarded_paths: set[str] = set()
        for guarded in _GUARDED_PATH_PREFIXES:
            if guarded in description:
                allowed_guarded_paths.add(guarded)

        try:
            # Step 1a: Parse `### Full Source:` blocks from agent outputs,
            # write them to disk, collect content for the commit.
            all_file_content: dict[str, str] = {}  # rel_path → content
            for agent_id, output_text in agent_outputs.items():
                if not output_text:
                    continue
                files = self._parse_and_write_files(
                    output_text, agent_id, root=effective_root,
                    request_id=request_id,
                )
                all_file_content.update(files)
                logger.info(
                    "files_written", agent=agent_id, count=len(files), files=list(files.keys())
                )

            # Step 1b: Pick up files that the agent edited via `search_replace`
            # tool calls (which write directly to disk, not via the text output).
            # Agents are instructed to list those paths in a `## Files Modified`
            # section at the end of their response. We parse that list, read
            # the current on-disk content for each path, and include them in
            # the commit alongside the Full Source emissions.
            #
            # This is the bridge between the surgical-edit tool and the GitHub
            # commit: without it, search_replace would update files locally
            # but the commit step wouldn't know to push them.
            for agent_id, output_text in agent_outputs.items():
                if not output_text:
                    continue
                tool_edited = self._parse_files_modified_section(output_text)
                for rel_path in tool_edited:
                    if rel_path in all_file_content:
                        continue  # Already captured via Full Source
                    full_path = effective_root / rel_path
                    if not full_path.exists():
                        logger.warning(
                            "files_modified_listed_but_missing",
                            agent=agent_id, path=rel_path,
                        )
                        continue
                    try:
                        content = full_path.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(
                            "files_modified_read_failed",
                            agent=agent_id, path=rel_path, error=str(e),
                        )
                        continue
                    all_file_content[rel_path] = content
                    logger.info(
                        "search_replace_file_picked_up",
                        agent=agent_id, path=rel_path,
                    )

            if not all_file_content:
                raise CodeWriteError("No code files were produced by any agent")

            # Guarded-path enforcement. Reject the commit if it includes any
            # file under a guarded prefix that the request didn't explicitly
            # name. Without this, agents have been self-modifying their own
            # YAML configs (REQ-8C3B4F edited backend_specialist.yaml,
            # REQ-D0742A edited devops_specialist.yaml) and adding off-scope
            # changes (REQ-D20A12 added a too-long comment to
            # test_config_validation.py).
            offending = []
            for rel_path in all_file_content:
                for guarded in _GUARDED_PATH_PREFIXES:
                    if rel_path.startswith(guarded) and guarded not in allowed_guarded_paths:
                        offending.append((rel_path, guarded))
                        break
            if offending:
                # Drop those paths from the commit instead of failing entirely.
                # Keep the legitimate file edits, log the rejections, surface a
                # clear error to the agent if NOTHING is left to commit. Doing
                # this as a soft-reject keeps the legitimate user-requested
                # changes (e.g. PromptStudio.tsx) shippable even when the agent
                # added an off-scope edit alongside.
                for path, guarded in offending:
                    logger.warning(
                        "code_writer_off_scope_path_rejected",
                        path=path, guarded_prefix=guarded,
                        request_id=request_id,
                    )
                    all_file_content.pop(path, None)
                if not all_file_content:
                    rejected_summary = ", ".join(p for p, _ in offending)
                    raise CodeWriteError(
                        f"Commit rejected — every emitted file is under a "
                        f"guarded path that the request didn't explicitly "
                        f"authorize: {rejected_summary}. Guarded prefixes: "
                        f"{', '.join(_GUARDED_PATH_PREFIXES)}. Re-emit only "
                        f"the files the user actually asked you to change.",
                    )

            all_files = list(all_file_content.keys())
            logger.info(
                "code_writer_materialized",
                request_id=request_id, file_count=len(all_files),
                files=all_files,
            )

            # Step 2: Compile Python code — only the files we just wrote (lint-my-diff).
            # Linting the whole src/ tree would surface pre-existing E/F violations
            # in code the agent never touched and block every commit.
            python_files = [f for f in all_files if f.endswith(".py")]
            if python_files:
                await self._compile_python(python_files, cwd=effective_root)
                logger.info(
                    "code_writer_python_compiled",
                    request_id=request_id, file_count=len(python_files),
                )

            # Step 3: Run the real prod frontend build (not just tsc --noEmit)
            # if frontend files were written. tsc --noEmit uses a permissive
            # config that ignores src/tests/ and missing modules; the supervisor's
            # docker build runs `npm run build` against the strict
            # tsconfig.app.json. Running the SAME command here means a commit
            # only lands if the prod build actually compiles. Without this,
            # agent-written test files (REQ-7C6527) pass commit-time but blow
            # up at staging deploy.
            has_frontend = any(f.startswith("frontend/") for f in all_files)
            if has_frontend:
                # Returns silently if npm isn't installed in this container —
                # supervisor's docker build catches TS errors as a backstop.
                # Raises CodeWriteError only when npm IS available AND the build
                # actually fails (real type errors, not env gaps).
                await self._compile_typescript(cwd=effective_root)
                logger.info(
                    "code_writer_typescript_compiled",
                    request_id=request_id,
                )

            # Step 4: Run tests — only when the agent specifically wrote/changed
            # TEST FILES. The lint-my-diff philosophy: only validate what THIS
            # commit touched. Running the whole pytest suite on every commit
            # (even ones that only change non-test source files) surfaces
            # pre-existing test breakage unrelated to the agent's change and
            # blocks legitimate commits. REQ-E791EB hit this — its scope was
            # prompts.py docstring + PromptStudio.tsx text, and the commit was
            # rejected by an unrelated pre-existing test_all_teams_loaded
            # failure that's been broken for the entire session.
            #
            # If the agent emitted a test file (tests/test_*.py), run just
            # those specific files. If the agent only changed non-test source
            # files, skip pytest entirely — the supervisor's docker build will
            # eventually catch import-level issues at runtime if any.
            test_files_written = [
                f for f in python_files
                if f.startswith("tests/") and "test_" in f.rsplit("/", 1)[-1]
            ]
            if test_files_written:
                await self._run_tests(test_files_written, cwd=effective_root)
                logger.info(
                    "code_writer_tests_passed",
                    request_id=request_id, test_files=test_files_written,
                )

            # Materialize succeeded — clear any drop-guard rejection
            # hashes we accumulated for this request so a future task
            # re-using the same request_id (re-dispatch path) starts
            # fresh. Without this, a legitimate edit that happens to
            # hash to a previously-rejected emission would be wrongly
            # flagged as a loop.
            keys_to_clear = [
                k for k in self._recent_drop_rejections
                if k[0] == request_id
            ]
            for k in keys_to_clear:
                del self._recent_drop_rejections[k]

            return all_file_content

        except CodeWriteError:
            raise
        except Exception as e:
            # Unexpected internal error inside materialize. Wrap as
            # CodeWriteError so the caller (orchestrator's materialize
            # handler) can route to rework with the standard
            # commit_status=failed contract.
            raise CodeWriteError(f"Materialize failed: {e}") from e

    async def _commit_to_github_only(
        self,
        request_id: str,
        description: str,
        all_file_content: dict[str, str],
        repo: str | None,
    ) -> DeploymentState:
        """Step 7-8 of the original pipeline: publish to GitHub +
        persist DeploymentState. Assumes ``all_file_content`` was
        already produced by ``materialize_files()`` — no parsing, no
        re-validation, no on-disk re-write. The files are already on
        disk and already lint/tested clean."""
        deployment_id = f"deploy-{uuid.uuid4().hex[:8]}"
        dep_state = DeploymentState(
            deployment_id=deployment_id,
            request_id=request_id,
        )
        try:
            all_files = list(all_file_content.keys())
            dep_state.files_committed = all_files
            self._record_step(
                dep_state, "files_written", "done", f"{len(all_files)} files written",
            )

            # Publish to GitHub via Trees API (atomic multi-file commit).
            file_list = "\n".join(f"- {f}" for f in all_files)
            commit_msg = (
                f"feat({request_id}): {description[:80]}\n\n"
                f"Files:\n{file_list}\n\n"
                f"Auto-committed by Agent Team pipeline"
            )
            try:
                commit_info = await self.github.commit_files(
                    {p: c for p, c in all_file_content.items()},
                    commit_msg,
                    repo=repo,
                )
            except GitHubPublishError as e:
                raise CodeWriteError(f"GitHub publish failed: {e}") from e

            dep_state.commit_sha = commit_info["short_sha"]
            dep_state.rollback_sha = commit_info["parent_sha"]  # parent = pre-commit HEAD
            dep_state.current_step = DeploymentStep.CODE_COMMITTED
            self._record_step(
                dep_state, "code_committed", "done",
                f"Published to GitHub: {commit_info['short_sha']}",
            )

            await self.state.create_deployment_state(dep_state)
            logger.info(
                "code_committed",
                request_id=request_id,
                sha=commit_info["short_sha"],
                url=commit_info["url"],
                files=len(all_files),
            )
            return dep_state

        except CodeWriteError:
            raise
        except Exception as e:
            dep_state.current_step = DeploymentStep.FAILED
            dep_state.error_message = str(e)
            self._record_step(dep_state, "failed", "error", str(e))
            try:
                await self.state.create_deployment_state(dep_state)
            except Exception:
                pass
            raise CodeWriteError(f"Code commit failed: {e}") from e

    def _parse_files_modified_section(self, output_text: str) -> list[str]:
        """Pull paths out of the agent's `## Files Modified` final-output block.

        Agents are required to emit a section at the end of their response:

            ## Files Modified
            - frontend/src/pages/PromptStudio.tsx
            - src/api/routes/prompts.py

        Pulls the bullet paths out. Robust to small format variations:
        - Tolerates `### Files Modified` and `# Files Modified` headers too.
        - Tolerates `- `, `* `, `1. ` bullet markers.
        - Strips backticks around paths.
        - Stops at the next heading.

        Returns an empty list if no section is present (agent emitted only
        Full Source blocks).
        """
        # Find the start of the section (case-insensitive header match).
        section_pattern = re.compile(
            r"^#{1,4}\s+Files\s+Modified\s*$",
            re.MULTILINE | re.IGNORECASE,
        )
        m = section_pattern.search(output_text)
        if not m:
            return []
        after = output_text[m.end():]
        # Stop at next markdown heading.
        end_match = re.search(r"^#{1,4}\s+\S", after, re.MULTILINE)
        section_body = after[: end_match.start()] if end_match else after

        paths: list[str] = []
        for line in section_body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Match common bullet styles.
            bullet_match = re.match(r"^[-*]\s+(.+)$", stripped) or re.match(
                r"^\d+\.\s+(.+)$", stripped,
            )
            if not bullet_match:
                continue
            raw_path = bullet_match.group(1).strip()
            # Strip backticks the agent may have wrapped the path in.
            cleaned = raw_path.strip("`").strip()
            # Reject obviously-not-a-path lines.
            if not cleaned or " " in cleaned or cleaned.startswith("("):
                continue
            # Security: same path-traversal check applied in _parse_and_write_files.
            if ".." in cleaned or cleaned.startswith("/"):
                logger.warning("files_modified_path_rejected", path=cleaned)
                continue
            paths.append(cleaned)
        return paths

    def _parse_and_write_files(
        self,
        output_text: str,
        agent_id: str,
        root: Path | None = None,
        request_id: str = "",
    ) -> dict[str, str]:
        """Parse code blocks with file paths from agent output, validate every
        one against the snapshot guard, THEN write to disk atomically (all-or-nothing).

        ``root`` overrides the writer's default base directory — used by
        per-project commits to land files under the project's working
        tree instead of the platform tree.

        Returns a dict of {path: content} for downstream use (e.g., GitHub publishing).
        Raises CodeWriteError if any file fails validation — and in that case NO
        files are written, so disk state is unchanged.
        """
        target_root = root if root is not None else self.root
        # Pattern: ### `path/to/file.ext` or ### Full Source: `path/to/file.ext`
        # Followed by ```lang\n...\n```
        pattern = r'###\s+(?:Full Source:\s*)?`([^`]+)`\s*(?:\([^)]*\))?\s*\n```\w*\n([\s\S]*?)```'
        matches = re.findall(pattern, output_text)

        # Phase 1: parse + validate. Collect every (full_path, rel_path, new_content)
        # triple and check each one against its prior on-disk content (if any).
        # If anything fails, raise BEFORE touching disk — partial commits are worse
        # than no commit at all because the failed agent's rework cycle can replay
        # cleanly.
        validated: list[tuple[Path, str, str]] = []
        for file_path, content in matches:
            file_path = file_path.strip()
            content = content.strip()

            if not file_path or not content:
                continue

            # Security: prevent path traversal
            if ".." in file_path or file_path.startswith("/"):
                logger.warning("path_traversal_blocked", path=file_path, agent=agent_id)
                continue

            full_path = target_root / file_path
            content_with_newline = content + "\n"

            if full_path.exists():
                old_text = full_path.read_text(encoding="utf-8")
                self._validate_safe_overwrite(
                    file_path=file_path,
                    agent_id=agent_id,
                    old_text=old_text,
                    new_text=content_with_newline,
                    request_id=request_id,
                )

            validated.append((full_path, file_path, content_with_newline))

        # Phase 2: write. Every file passed validation, so it's safe to flush
        # them all to disk.
        files_written: dict[str, str] = {}
        for full_path, file_path, content_with_newline in validated:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content_with_newline, encoding="utf-8")
            files_written[file_path] = content_with_newline
            logger.debug("file_written", path=file_path, size=len(content_with_newline))

        return files_written

    def _validate_safe_overwrite(
        self,
        file_path: str,
        agent_id: str,
        old_text: str,
        new_text: str,
        request_id: str = "",
    ) -> None:
        """Refuse to overwrite an existing file if the new content looks like a
        patch fragment or a suspicious truncation.

        Two heuristics, applied independently:

        1. **Marker check.** New content (case-insensitive) contains any of
           ``_SUSPICIOUS_MARKERS`` — phrases like "PATCH SCOPE", "splice these
           in-place", "rest of file unchanged" that signal the agent emitted
           a fragment thinking the system would patch.
        2. **Line-count drop check.** Existing file has ≥ ``_MIN_LINES_FOR_DROP_CHECK``
           lines AND the new content has fewer than ``(100 - _MAX_LINE_DROP_PCT)``
           percent of that. Tiny files are exempt (the percentage is noisy for
           small file sizes).

        Raises CodeWriteError on rejection. Caller MUST treat this as a hard
        stop — do not write the file partially.
        """
        lower_new = new_text.lower()
        for marker in _SUSPICIOUS_MARKERS:
            if marker in lower_new:
                logger.error(
                    "code_writer_patch_marker_detected",
                    file=file_path, agent=agent_id, marker=marker,
                )
                raise CodeWriteError(
                    f"Refusing to overwrite '{file_path}': new content contains "
                    f"the marker {marker!r}, which means the agent emitted a "
                    f"patch fragment / abbreviated file. CodeWriter does WHOLE-FILE "
                    f"replacement (no patch semantics) — partial output silently "
                    f"deletes the rest of the file. Re-emit the COMPLETE file "
                    f"content with every original line that wasn't intentionally "
                    f"removed."
                )

        old_lines = old_text.count("\n")
        new_lines = new_text.count("\n")
        if old_lines >= _MIN_LINES_FOR_DROP_CHECK and new_lines < old_lines:
            drop_pct = (old_lines - new_lines) / old_lines * 100
            if drop_pct >= _MAX_LINE_DROP_PCT:
                # ── Config-seed exempt list ──
                # Config files where the agent has authority to refactor —
                # e.g. splitting tsconfig.json into the project-references
                # pattern with two siblings. The marker check above still
                # applies to these (so an accidental "rest of file unchanged"
                # fragment still gets caught), but a large legitimate shrink
                # doesn't fail the commit.
                basename = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if basename in _DROP_GUARD_EXEMPT_BASENAMES:
                    logger.info(
                        "code_writer_line_count_drop_exempt",
                        file=file_path, agent=agent_id,
                        old_lines=old_lines, new_lines=new_lines,
                        drop_pct=round(drop_pct, 1),
                        reason="config_seed_basename",
                    )
                else:
                    logger.error(
                        "code_writer_line_count_drop",
                        file=file_path, agent=agent_id,
                        old_lines=old_lines, new_lines=new_lines,
                        drop_pct=round(drop_pct, 1),
                    )
                    # ── Same-content loop detection ──
                    # If the agent submitted byte-identical content for the
                    # same (request_id, file_path) on the previous cycle, the
                    # rework prompt has clearly failed to change behaviour.
                    # Escalate with a much louder message so the agent has
                    # a different (more actionable) prompt next cycle. This
                    # closes T-103e9025's failure class — 3 cycles all
                    # emitting the same 275-line shrink of a 764-line file.
                    new_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
                    key = (request_id, file_path) if request_id else None
                    if key and self._recent_drop_rejections.get(key) == new_hash:
                        logger.error(
                            "code_writer_drop_guard_loop_detected",
                            file=file_path, agent=agent_id,
                            request_id=request_id,
                            old_lines=old_lines, new_lines=new_lines,
                        )
                        raise CodeWriteError(
                            f"🚨 DROP-GUARD LOOP for '{file_path}': you submitted "
                            f"BYTE-IDENTICAL content twice in a row that shrinks "
                            f"the file from {old_lines} → {new_lines} lines "
                            f"({drop_pct:.0f}% reduction). The previous cycle's "
                            f"rejection message already explained the fix — "
                            f"re-emitting the same bytes will fail again every "
                            f"cycle until you run out of budget.\n\n"
                            f"YOU MUST CHANGE STRATEGY THIS TURN. Pick exactly ONE:\n"
                            f"  (A) Use `search_replace` (not `### File:` blocks) "
                            f"for the specific edit you want. search_replace is "
                            f"diff-based and bypasses this guard entirely.\n"
                            f"  (B) Re-read the existing file via `file_read` FIRST, "
                            f"then emit a FULL rewrite that includes every line you "
                            f"don't intend to delete. The current file has "
                            f"{old_lines} lines; your last two emissions had "
                            f"{new_lines}. That gap is what needs to disappear.\n\n"
                            f"Identical content on the next cycle will be treated "
                            f"as a permanent failure (no further rework granted)."
                        )
                    # First-time rejection: remember the hash so we can
                    # detect a repeat next cycle.
                    if key:
                        self._recent_drop_rejections[key] = new_hash
                    # Message designed to be consumed by the rework agent.
                    # Lists three concrete options + tells the agent that
                    # the current on-disk content will be appended by the
                    # orchestrator's _enrich_error_with_line_snippets, so
                    # the agent knows it has full visibility before
                    # choosing a path. The runner injects this string
                    # verbatim into rework_instructions.
                    raise CodeWriteError(
                        f"Refusing to overwrite '{file_path}': line count dropped "
                        f"from {old_lines} to {new_lines} ({drop_pct:.0f}% reduction). "
                        f"CodeWriter does WHOLE-FILE replacement; a short emission "
                        f"deletes the rest. Pick ONE of these three fixes:\n"
                        f"  (1) MERGE: re-emit the complete file with your changes "
                        f"applied on top of the existing content. The current on-disk "
                        f"content is shown below — copy the lines you want to keep.\n"
                        f"  (2) SURGICAL: use `search_replace` instead of "
                        f"`### Full Source:` for the specific edit you intended. "
                        f"search_replace bypasses this guard since it's diff-based.\n"
                        f"  (3) SPLIT: if you're refactoring this file into multiple "
                        f"siblings (e.g. splitting tsconfig.json into "
                        f"tsconfig.app.json + tsconfig.node.json), emit ALL the new "
                        f"sibling files in the same response — the system will then "
                        f"see the original lines have moved, not vanished.\n"
                        f"Do NOT just re-emit the same short version a second time — "
                        f"the guard will fire again. Threshold: "
                        f"{_MAX_LINE_DROP_PCT}% drop on files ≥{_MIN_LINES_FOR_DROP_CHECK} lines."
                    )

    async def _compile_python(self, files: list[str], cwd: Path | None = None) -> None:
        """Run ruff check on the specific Python files the agent just wrote.

        We deliberately do NOT check the whole `src/` tree — pre-existing E/F
        violations in code the current agent never touched would block every
        commit. Only the files in this commit are validated.

        ``cwd`` overrides the writer's default base directory — used for
        per-project commits so ruff finds the project's pyproject.toml
        (if any) instead of the platform's.
        """
        if not files:
            return
        # Shell-quote each path to be safe with unusual characters.
        quoted = " ".join(f"'{f}'" for f in files)
        code, stdout, stderr = await self._run_cmd(
            f"ruff check {quoted} --select E,F --no-fix", timeout=30, cwd=cwd,
        )
        if code != 0:
            error = stderr or stdout
            raise CodeWriteError(f"Python compilation failed (ruff):\n{error[:500]}")

    async def _compile_typescript(self, cwd: Path | None = None) -> None:
        """Run the REAL prod frontend build (npm run build) if npm is available.

        Uses the strict tsconfig.app.json that the supervisor's docker build
        will use, so any commit that wouldn't survive `docker compose build`
        gets rejected here instead of failing at staging deploy.

        Backend containers don't always ship with node/npm (it's a Python
        image). When npm isn't present, we skip the local check and rely on
        the supervisor's `docker compose build` to catch compile errors.

        For per-project commits the project tree won't have
        ``frontend/node_modules`` (npm install only runs at
        ``docker compose build`` time, which is the Deploy step). When
        node_modules is missing we skip the build — the per-project
        docker build catches errors at deploy time. Without this guard
        every per-project frontend commit would fail with "vite: not
        found", blocking the agent forever.
        """
        # Probe for npm. The path traversal-safe way: try `npm --version` and
        # tolerate non-zero exit. If it succeeds, npm exists; otherwise skip.
        probe_code, _, _ = await self._run_cmd("npm --version", timeout=10, cwd=cwd)
        if probe_code != 0:
            logger.info(
                "ts_compile_skipped_no_npm",
                reason="npm not available in this container — supervisor will catch TS errors at docker build time",
            )
            return

        # Skip when node_modules isn't installed in the target tree —
        # almost certainly a per-project commit before the first Deploy.
        target_root = cwd if cwd is not None else self.root
        if not (target_root / "frontend" / "node_modules").exists():
            logger.info(
                "ts_compile_skipped_no_node_modules",
                root=str(target_root),
                reason="per-project commit; docker build will install deps at Deploy time",
            )
            return

        code, stdout, stderr = await self._run_cmd(
            "cd frontend && npm run build 2>&1", timeout=180, cwd=cwd,
        )
        if code != 0:
            error = stderr or stdout
            raise CodeWriteError(f"Frontend prod build failed:\n{error[:1000]}")

    async def _run_tests(
        self,
        test_files: list[str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        """Run pytest only on the specific test files the agent emitted.

        Running pytest tests/ (the whole suite) is wrong for the lint-my-diff
        philosophy — pre-existing test failures (e.g. test_all_teams_loaded
        with a stale 8/9 agent count) block every commit even when the agent's
        change is unrelated. Pass a list of test files; if None, default to
        the whole suite for backward compatibility.
        """
        target = " ".join(test_files) if test_files else "tests/"
        code, stdout, stderr = await self._run_cmd(
            f"python -m pytest {target} -x -q --tb=short --no-cov 2>&1",
            timeout=120, cwd=cwd,
        )
        if code != 0:
            error = stderr or stdout
            raise CodeWriteError(f"Tests failed:\n{error[:500]}")

    def _record_step(self, state: DeploymentState, step: str, status: str, detail: str) -> None:
        """Record a step in the deployment history."""
        state.step_history.append({
            "step": step,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "detail": detail,
        })

    async def _run_cmd(
        self,
        cmd: str,
        timeout: int = 30,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        """Run a shell command and return (exit_code, stdout, stderr).

        ``cwd`` overrides ``self.root`` for this call — used by the
        per-project compile / test pipeline so ruff / npm / pytest run
        in the project's working tree, not the platform's.
        """
        target_cwd = cwd if cwd is not None else self.root
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(target_cwd),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode or 0, stdout.decode(), stderr.decode()
        except asyncio.TimeoutError:
            return 1, "", f"Command timed out after {timeout}s: {cmd}"
        except Exception as e:
            return 1, "", str(e)
