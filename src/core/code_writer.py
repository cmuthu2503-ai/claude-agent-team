"""Code Writer — parses agent code output, writes files, compiles, publishes to GitHub.

Compilation/test steps use ruff/tsc/pytest binaries that ARE installed in the
backend container. Git publishing uses the GitHub Trees API (no `git` CLI), so
this module works without git installed in the container.
"""

import asyncio
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

    async def commit_code(
        self, request_id: str, description: str, agent_outputs: dict[str, str]
    ) -> DeploymentState:
        """Full code commit pipeline: parse → write → compile → test → publish.

        Args:
            request_id: The request ID this code belongs to
            description: Human-readable description for the commit message
            agent_outputs: Dict of agent_id → output_text (from backend/frontend specialists)

        Returns:
            DeploymentState with step = code_committed (ready for sidecar)

        Raises:
            CodeWriteError if any step fails
        """
        deployment_id = f"deploy-{uuid.uuid4().hex[:8]}"
        dep_state = DeploymentState(
            deployment_id=deployment_id,
            request_id=request_id,
        )
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
                files = self._parse_and_write_files(output_text, agent_id)
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
                    full_path = self.root / rel_path
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
            dep_state.files_committed = all_files
            self._record_step(dep_state, "files_written", "done", f"{len(all_files)} files written")

            # Step 2: Compile Python code — only the files we just wrote (lint-my-diff).
            # Linting the whole src/ tree would surface pre-existing E/F violations
            # in code the agent never touched and block every commit.
            python_files = [f for f in all_files if f.endswith(".py")]
            if python_files:
                await self._compile_python(python_files)
                self._record_step(
                    dep_state, "python_compiled", "done",
                    f"ruff check passed on {len(python_files)} file(s)",
                )
            else:
                self._record_step(
                    dep_state, "python_compiled", "skipped",
                    "no Python files in this commit",
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
                await self._compile_typescript()
                self._record_step(
                    dep_state, "typescript_compiled", "done",
                    "frontend prod build verified (or skipped if npm unavailable)",
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
                await self._run_tests(test_files_written)
                self._record_step(dep_state, "tests_passed", "done", "pytest passed")
            else:
                self._record_step(
                    dep_state, "tests_passed", "skipped",
                    "frontend-only commit — pytest not relevant",
                )

            # Step 5: Publish to GitHub via Trees API (atomic multi-file commit)
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

            # Save state — sidecar will pick this up
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

    def _parse_and_write_files(self, output_text: str, agent_id: str) -> dict[str, str]:
        """Parse code blocks with file paths from agent output, validate every
        one against the snapshot guard, THEN write to disk atomically (all-or-nothing).

        Returns a dict of {path: content} for downstream use (e.g., GitHub publishing).
        Raises CodeWriteError if any file fails validation — and in that case NO
        files are written, so disk state is unchanged.
        """
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

            full_path = self.root / file_path
            content_with_newline = content + "\n"

            if full_path.exists():
                old_text = full_path.read_text(encoding="utf-8")
                self._validate_safe_overwrite(
                    file_path=file_path,
                    agent_id=agent_id,
                    old_text=old_text,
                    new_text=content_with_newline,
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

    @staticmethod
    def _validate_safe_overwrite(
        file_path: str,
        agent_id: str,
        old_text: str,
        new_text: str,
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
                logger.error(
                    "code_writer_line_count_drop",
                    file=file_path, agent=agent_id,
                    old_lines=old_lines, new_lines=new_lines,
                    drop_pct=round(drop_pct, 1),
                )
                raise CodeWriteError(
                    f"Refusing to overwrite '{file_path}': line count dropped "
                    f"from {old_lines} to {new_lines} ({drop_pct:.0f}% reduction). "
                    f"This usually means the agent emitted a patch fragment or "
                    f"accidentally truncated the file. CodeWriter does whole-file "
                    f"replacement — emitting a short version DELETES the rest. "
                    f"If the shrink is genuinely intended (large refactor), the "
                    f"agent must re-emit the complete new file. Threshold: "
                    f"{_MAX_LINE_DROP_PCT}% drop on files ≥{_MIN_LINES_FOR_DROP_CHECK} lines."
                )

    async def _compile_python(self, files: list[str]) -> None:
        """Run ruff check on the specific Python files the agent just wrote.

        We deliberately do NOT check the whole `src/` tree — pre-existing E/F
        violations in code the current agent never touched would block every
        commit. Only the files in this commit are validated.
        """
        if not files:
            return
        # Shell-quote each path to be safe with unusual characters.
        quoted = " ".join(f"'{f}'" for f in files)
        code, stdout, stderr = await self._run_cmd(
            f"ruff check {quoted} --select E,F --no-fix", timeout=30,
        )
        if code != 0:
            error = stderr or stdout
            raise CodeWriteError(f"Python compilation failed (ruff):\n{error[:500]}")

    async def _compile_typescript(self) -> None:
        """Run the REAL prod frontend build (npm run build) if npm is available.

        Uses the strict tsconfig.app.json that the supervisor's docker build
        will use, so any commit that wouldn't survive `docker compose build`
        gets rejected here instead of failing at staging deploy.

        Backend containers don't always ship with node/npm (it's a Python
        image). When npm isn't present, we skip the local check and rely on
        the supervisor's `docker compose build` to catch compile errors. The
        supervisor-side build is the same content; the only thing we lose
        when npm is missing is the EARLIER catch + rework-with-instructions
        path. Better than failing the commit on an environment gap.
        """
        # Probe for npm. The path traversal-safe way: try `npm --version` and
        # tolerate non-zero exit. If it succeeds, npm exists; otherwise skip.
        probe_code, _, _ = await self._run_cmd("npm --version", timeout=10)
        if probe_code != 0:
            logger.info(
                "ts_compile_skipped_no_npm",
                reason="npm not available in this container — supervisor will catch TS errors at docker build time",
            )
            return

        code, stdout, stderr = await self._run_cmd(
            "cd frontend && npm run build 2>&1", timeout=180,
        )
        if code != 0:
            error = stderr or stdout
            raise CodeWriteError(f"Frontend prod build failed:\n{error[:1000]}")

    async def _run_tests(self, test_files: list[str] | None = None) -> None:
        """Run pytest only on the specific test files the agent emitted.

        Running pytest tests/ (the whole suite) is wrong for the lint-my-diff
        philosophy — pre-existing test failures (e.g. test_all_teams_loaded
        with a stale 8/9 agent count) block every commit even when the agent's
        change is unrelated. Pass a list of test files; if None, default to
        the whole suite for backward compatibility.
        """
        target = " ".join(test_files) if test_files else "tests/"
        code, stdout, stderr = await self._run_cmd(
            f"python -m pytest {target} -x -q --tb=short --no-cov 2>&1", timeout=120,
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

    async def _run_cmd(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """Run a shell command and return (exit_code, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode or 0, stdout.decode(), stderr.decode()
        except asyncio.TimeoutError:
            return 1, "", f"Command timed out after {timeout}s: {cmd}"
        except Exception as e:
            return 1, "", str(e)
