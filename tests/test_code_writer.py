"""Tests for the CodeWriter — focus on the snapshot/validate guard (Option C
defense-in-depth against patch-fragment agent output that would otherwise
silently delete most of a file)."""

from pathlib import Path

import pytest

from src.core.code_writer import CodeWriteError, CodeWriter


@pytest.fixture
def writer(tmp_path: Path) -> CodeWriter:
    """A CodeWriter rooted at a temp dir so tests don't touch the real repo.

    The CodeWriter constructor needs a StateStore for db calls during full
    commit_code() runs — but the snapshot/validate path only touches the
    filesystem, so a None state is safe here.
    """
    return CodeWriter(state=None, project_root=str(tmp_path))  # type: ignore[arg-type]


# ── New-file emission: validation is skipped (nothing to clobber) ────────────


def test_new_file_no_validation(writer: CodeWriter, tmp_path: Path) -> None:
    """Creating a brand-new file under any circumstance is allowed — the
    snapshot guard only fires on overwrites of existing files."""
    output = """
### `frontend/src/components/Brand.tsx`
```tsx
export const Brand = () => <div>Hi</div>
```
"""
    files = writer._parse_and_write_files(output, "frontend_specialist")
    assert "frontend/src/components/Brand.tsx" in files
    assert (tmp_path / "frontend/src/components/Brand.tsx").exists()


# ── Suspicious-marker detection (the REQ-98E4C2 failure mode) ────────────────


@pytest.mark.parametrize(
    "marker_phrase",
    [
        "PATCH SCOPE — Y2K THEME ONLY",
        "splice these in-place over the existing y2k blocks",
        "the patcher MUST",
        "rest of file unchanged",
        "// ... existing code ...",
        "/* ... existing imports ... */",
        "# ... existing helpers ...",
    ],
)
def test_patch_marker_rejects_overwrite(
    writer: CodeWriter, tmp_path: Path, marker_phrase: str,
) -> None:
    """Any phrasing that suggests the agent emitted a fragment should reject
    the whole commit, leaving the original file intact."""
    target = tmp_path / "frontend/src/themes.css"
    target.parent.mkdir(parents=True)
    original = "[data-theme=\"linear\"] { --bg: #000; }\n" * 50  # 50 lines
    target.write_text(original)

    bad_output = f"""
### `frontend/src/themes.css`
```css
/* {marker_phrase} */
[data-theme="y2k"] {{ --bg: #0a0f0a; }}
```
"""
    with pytest.raises(CodeWriteError) as exc:
        writer._parse_and_write_files(bad_output, "frontend_specialist")

    # Original content must be untouched on disk.
    assert target.read_text() == original
    # Error message should mention the offending marker (lowercase form).
    assert "marker" in str(exc.value).lower()


# ── Line-count drop heuristic ────────────────────────────────────────────────


def test_minor_shrink_allowed(writer: CodeWriter, tmp_path: Path) -> None:
    """A 30% line-count drop on a large file is below the 50% threshold —
    legitimate refactors look like this, allow them."""
    target = tmp_path / "frontend/src/old.tsx"
    target.parent.mkdir(parents=True)
    target.write_text("\n".join(["// line"] * 100) + "\n")  # 100 lines

    new_content = "\n".join(["// line"] * 70) + "\n"  # 70 lines = 30% drop
    output = f"""
### `frontend/src/old.tsx`
```tsx
{new_content}```
"""
    files = writer._parse_and_write_files(output, "frontend_specialist")
    assert "frontend/src/old.tsx" in files
    assert target.read_text().count("\n") == 70


def test_major_shrink_rejected(writer: CodeWriter, tmp_path: Path) -> None:
    """A 76% drop (the REQ-98E4C2 themes.css case: 353 → 85 lines) must be
    rejected. The file on disk stays at its prior content."""
    target = tmp_path / "frontend/src/themes.css"
    target.parent.mkdir(parents=True)
    original = "\n".join([f"/* line {i} */" for i in range(353)]) + "\n"
    target.write_text(original)

    truncated = "\n".join([f"/* line {i} */" for i in range(85)]) + "\n"
    bad_output = f"""
### `frontend/src/themes.css`
```css
{truncated}```
"""
    with pytest.raises(CodeWriteError) as exc:
        writer._parse_and_write_files(bad_output, "frontend_specialist")

    assert target.read_text() == original  # disk untouched
    msg = str(exc.value).lower()
    assert "line count" in msg or "dropped" in msg


def test_config_seed_tsconfig_can_shrink_to_project_references_pattern(
    writer: CodeWriter, tmp_path: Path,
) -> None:
    """REQ-F86080 regression. The scaffold's legacy 21-line tsconfig.json
    being replaced by the modern 7-line project-references pattern must
    succeed — tsconfig.json is in _DROP_GUARD_EXEMPT_BASENAMES so the
    line-drop guard doesn't fire on it even at 67% reduction.

    The marker-based guard still applies (this test doesn't have any
    suspicious markers in the new content)."""
    target = tmp_path / "frontend/tsconfig.json"
    target.parent.mkdir(parents=True)
    # 21-line legacy monolithic tsconfig (scaffold default)
    legacy = (
        '{\n'
        '  "compilerOptions": {\n'
        '    "target": "ES2022",\n'
        '    "useDefineForClassFields": true,\n'
        '    "lib": ["ES2022", "DOM", "DOM.Iterable"],\n'
        '    "module": "ESNext",\n'
        '    "skipLibCheck": true,\n'
        '    "moduleResolution": "bundler",\n'
        '    "allowImportingTsExtensions": true,\n'
        '    "resolveJsonModule": true,\n'
        '    "isolatedModules": true,\n'
        '    "moduleDetection": "force",\n'
        '    "noEmit": true,\n'
        '    "jsx": "react-jsx",\n'
        '    "strict": true,\n'
        '    "noUnusedLocals": false,\n'
        '    "noUnusedParameters": false,\n'
        '    "noFallthroughCasesInSwitch": true\n'
        '  },\n'
        '  "include": ["src", "vite.config.ts"]\n'
        '}\n'
    )
    target.write_text(legacy)
    assert legacy.count("\n") == 21

    # 7-line modern project-references root config
    modern = """
### `frontend/tsconfig.json`
```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
```
"""
    files = writer._parse_and_write_files(modern, "frontend_specialist")
    assert "frontend/tsconfig.json" in files
    # Disk reflects the new shape, not the legacy
    new_content = target.read_text()
    assert '"files": []' in new_content
    assert '"references"' in new_content
    assert "compilerOptions" not in new_content  # legacy gone


def test_config_seed_pyproject_can_shrink(
    writer: CodeWriter, tmp_path: Path,
) -> None:
    """Backend-side counterpart: pyproject.toml is in the exempt list so
    the scaffold's verbose starter can be replaced by a leaner one
    without triggering the guard."""
    target = tmp_path / "pyproject.toml"
    target.write_text("\n".join([f'# line {i}' for i in range(40)]) + "\n")  # 40 lines

    new_output = """
### `pyproject.toml`
```toml
[project]
name = "app"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
line-length = 100
```
"""
    files = writer._parse_and_write_files(new_output, "backend_specialist")
    assert "pyproject.toml" in files
    assert "[tool.ruff]" in target.read_text()


def test_config_seed_exempt_still_catches_patch_marker(
    writer: CodeWriter, tmp_path: Path,
) -> None:
    """Exempt basename does NOT bypass the marker check — an agent emitting
    `# ... existing ...` is still a fragment, regardless of file type."""
    target = tmp_path / "pyproject.toml"
    target.write_text("\n".join([f'# line {i}' for i in range(40)]) + "\n")

    bad_output = """
### `pyproject.toml`
```toml
[project]
name = "app"
# ... existing code ...
```
"""
    with pytest.raises(CodeWriteError) as exc:
        writer._parse_and_write_files(bad_output, "backend_specialist")
    assert "marker" in str(exc.value).lower() or "fragment" in str(exc.value).lower()


async def test_materialize_files_writes_to_disk_and_returns_dict(
    writer: CodeWriter, tmp_path: Path,
) -> None:
    """Fix A: materialize_files is the new public method that writes
    agent emissions to disk BEFORE review/test. Confirms it (a) writes
    the file, (b) returns the {path: content} dict, (c) doesn't talk
    to GitHub or persist any DeploymentState (those belong to the
    later commit_to_github stage)."""
    output = """
### `frontend/src/Brand.tsx`
```tsx
export const Brand = () => <div>Hi</div>
```

## Files Modified
- frontend/src/Brand.tsx
"""
    files = await writer.materialize_files(
        request_id="REQ-MAT-1",
        description="Test materialize",
        agent_outputs={"frontend_specialist_cycle_00": output},
    )
    # File landed on disk
    assert (tmp_path / "frontend/src/Brand.tsx").exists()
    # Dict returned with content keyed by path
    assert "frontend/src/Brand.tsx" in files
    assert "Brand" in files["frontend/src/Brand.tsx"]
    # No DeploymentState was persisted (commit_to_github wasn't called)
    # — verified by the fact that writer.state is None in the fixture
    # and we got here without an AttributeError.


async def test_commit_code_short_circuits_when_given_materialized_files(
    writer: CodeWriter, tmp_path: Path, monkeypatch,
) -> None:
    """Fix A: when ``materialized_files`` is passed to commit_code, the
    materialize half is skipped — no re-parsing, no re-writing, no
    repeat lint. This is what the workflow runner does on the new
    path: materialize once after development, commit once at the
    code_commit stage with the same files.

    Verified by checking that commit_code goes straight to GitHub
    publish without ever touching `_parse_and_write_files`."""
    parse_calls: list = []
    original_parse = writer._parse_and_write_files
    monkeypatch.setattr(
        writer, "_parse_and_write_files",
        lambda *a, **kw: parse_calls.append(a) or original_parse(*a, **kw),
    )

    # Stub the github publish to avoid a real network call.
    async def _fake_commit(files, msg, repo=None, branch=None):
        return {
            "sha": "deadbeef" * 5, "short_sha": "deadbeef",
            "url": "https://github.com/test/test/commit/deadbeef",
            "parent_sha": "cafebabe",
        }
    monkeypatch.setattr(writer.github, "commit_files", _fake_commit)

    # Stub state.create_deployment_state — we don't need DB writes.
    class _StubState:
        async def create_deployment_state(self, state):
            return None
    writer.state = _StubState()

    pre_materialized = {
        "frontend/src/App.tsx": "export default function App(){return null}\n",
    }

    dep_state = await writer.commit_code(
        request_id="REQ-MAT-2",
        description="Test short-circuit",
        agent_outputs={"backend_specialist_cycle_00": "(unused — short-circuit)"},
        materialized_files=pre_materialized,
    )
    # The parser was NOT called — short-circuit worked.
    assert parse_calls == []
    # We still got a DeploymentState back with commit details.
    assert dep_state.commit_sha == "deadbeef"
    assert "frontend/src/App.tsx" in dep_state.files_committed


def test_non_exempt_config_filename_still_guarded(
    writer: CodeWriter, tmp_path: Path,
) -> None:
    """A file that LOOKS like config but isn't in the exempt list still
    triggers the drop guard. Catches accidental exempt-list bypasses
    (e.g. tsconfig.foo.json with a typo)."""
    target = tmp_path / "tsconfig.weird.json"
    target.write_text("\n".join([f"// {i}" for i in range(40)]) + "\n")  # 40 lines

    truncated = """
### `tsconfig.weird.json`
```json
{ "foo": "bar" }
```
"""
    with pytest.raises(CodeWriteError) as exc:
        writer._parse_and_write_files(truncated, "frontend_specialist")
    assert "line count" in str(exc.value).lower()


def test_small_file_can_shrink_freely(writer: CodeWriter, tmp_path: Path) -> None:
    """A file with fewer than _MIN_LINES_FOR_DROP_CHECK lines (default 20) is
    exempt from the percentage check — tiny-file percentages are too noisy."""
    target = tmp_path / "frontend/src/tiny.ts"
    target.parent.mkdir(parents=True)
    target.write_text("\n".join([f"// {i}" for i in range(10)]) + "\n")  # 10 lines

    # Drop from 10 → 3 lines (70% reduction). Should still pass because the
    # original file is under the line-count floor.
    output = """
### `frontend/src/tiny.ts`
```ts
// only
// three
// lines
```
"""
    files = writer._parse_and_write_files(output, "frontend_specialist")
    assert "frontend/src/tiny.ts" in files


# ── Atomic two-phase write ───────────────────────────────────────────────────


def test_atomic_write_one_bad_aborts_all(
    writer: CodeWriter, tmp_path: Path,
) -> None:
    """If the agent emits two files and the SECOND one fails validation, the
    FIRST one must not have been written — validation happens before any disk
    writes."""
    # Pre-existing themes.css that the second emission will try to shrink.
    target = tmp_path / "frontend/src/themes.css"
    target.parent.mkdir(parents=True)
    original = "\n".join([f"line {i}" for i in range(100)]) + "\n"
    target.write_text(original)

    # First file: a brand-new component. By itself this would write fine.
    # Second file: a major shrink of themes.css. This must abort the whole batch.
    output = """
### `frontend/src/components/NewThing.tsx`
```tsx
export const NewThing = () => <div>new</div>
```

### `frontend/src/themes.css`
```css
/* only 5 lines */
[data-theme="y2k"] { --bg: #000; }
[data-theme="y2k"] { --bg: #001; }
[data-theme="y2k"] { --bg: #002; }
```
"""
    with pytest.raises(CodeWriteError):
        writer._parse_and_write_files(output, "frontend_specialist")

    # Neither file should have been written: themes.css unchanged AND NewThing
    # never created.
    assert target.read_text() == original
    assert not (tmp_path / "frontend/src/components/NewThing.tsx").exists()


# ── Path traversal guard is unchanged ────────────────────────────────────────


def test_path_traversal_still_blocked(writer: CodeWriter, tmp_path: Path) -> None:
    """The existing path-traversal guard runs before the snapshot check —
    `..` paths are silently skipped (logged), not raised."""
    output = """
### `../escape.txt`
```
attacker
```
"""
    files = writer._parse_and_write_files(output, "frontend_specialist")
    assert files == {}
