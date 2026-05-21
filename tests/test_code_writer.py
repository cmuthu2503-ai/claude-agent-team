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
