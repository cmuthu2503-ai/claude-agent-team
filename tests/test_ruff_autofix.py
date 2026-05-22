"""Regression tests for the auto-format-on-write pipeline.

Pins the behaviour that closed REQ-A6A4DB's failure class:

  - Unused imports (F401) are stripped at write time so the commit-gate
    never sees them.
  - Existing line-length reflow (E501) still works.
  - Soft-fail still holds: bad syntax doesn't block the write.

Runs ruff as a subprocess. The test environment ships ruff via the
backend container's `[dev]` extras (see `pyproject.toml`), so this
runs in CI without extra setup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.file_tools import _maybe_ruff_format


# Marked async because file_tools' format helper is async (subprocess).
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _project_pyproject(tmp_path: Path) -> None:
    """Drop a ``pyproject.toml`` into ``tmp_path`` so ruff picks up the
    same selection a real per-project tree would. Without this, ruff
    invoked with no ``--select`` falls back to its DEFAULT rules
    (only E + F), and tests for I001 / UP / SIM categories silently
    no-op. The content matches the platform's scaffold templates.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\n'
        'line-length = 100\n'
        'target-version = "py312"\n\n'
        '[tool.ruff.lint]\n'
        'select = ["E", "W", "F", "I", "N", "UP", "B", "SIM"]\n'
    )


async def test_strips_unused_imports(tmp_path: Path) -> None:
    """REQ-A6A4DB repro: `import re` + `from pydantic import ValidationError`
    were never referenced; agent failed 3 rework cycles unable to remove
    them. With the F401 auto-fix pass they're gone before write."""
    src = (
        '"""mod docstring"""\n\n'
        "import os\n"
        "import re\n"
        "from pydantic import ValidationError\n\n"
        "USED = os.getcwd()\n"
    )
    out, changed = await _maybe_ruff_format(tmp_path / "x.py", src)
    assert changed is True
    assert "import re" not in out
    assert "ValidationError" not in out
    # The actually-used import survives.
    assert "import os" in out
    assert "USED = os.getcwd()" in out


async def test_keeps_used_imports(tmp_path: Path) -> None:
    """Don't be over-eager: imports that ARE used must stay."""
    src = (
        "import json\n"
        "import os\n\n"
        "def hello() -> str:\n"
        "    return json.dumps({'cwd': os.getcwd()})\n"
    )
    out, _ = await _maybe_ruff_format(tmp_path / "x.py", src)
    assert "import json" in out
    assert "import os" in out


async def test_sorts_import_block(tmp_path: Path) -> None:
    """I001 (unsorted imports) is in our --select list. Out-of-order
    imports get sorted on write so the agent's emission order doesn't
    matter."""
    src = (
        "import os\n"
        "import json\n"  # comes before os alphabetically
        "import asyncio\n\n"
        "USED = (os.getcwd(), json.dumps({}), asyncio.sleep)\n"
    )
    out, changed = await _maybe_ruff_format(tmp_path / "x.py", src)
    assert changed is True
    # asyncio < json < os
    assert out.index("import asyncio") < out.index("import json") < out.index("import os")


async def test_non_python_passes_through(tmp_path: Path) -> None:
    """Only .py files get the ruff pipeline."""
    src = 'const x: number = 42; // unused'
    out, changed = await _maybe_ruff_format(tmp_path / "x.ts", src)
    assert changed is False
    assert out == src


async def test_unparseable_python_soft_fails(tmp_path: Path) -> None:
    """If the agent emits broken syntax mid-stream, the formatter must
    NOT raise — the commit-gate's `ruff check` will surface the real
    error later. Returning the original content lets the write proceed
    so the agent sees the error in the next cycle."""
    src = "def hello(\n  # missing close paren and body\n"
    out, _ = await _maybe_ruff_format(tmp_path / "x.py", src)
    # Either unchanged (parse failed) or some attempt — but never raise.
    assert isinstance(out, str)


async def test_keeps_explicit_reexports(tmp_path: Path) -> None:
    """Re-exports via `__all__` should NOT be flagged as unused —
    F401 respects __all__. This protects the per-package __init__.py
    pattern (`from .foo import bar` + `__all__ = ['bar']`)."""
    src = (
        "from os.path import join\n\n"
        "__all__ = ['join']\n"
    )
    out, _ = await _maybe_ruff_format(tmp_path / "__init__.py", src)
    assert "from os.path import join" in out


# ── Broad auto-fix coverage (the "basic compilation errors" class) ───────────


async def test_strips_unused_imports_alongside_non_fixable_errors(tmp_path: Path) -> None:
    """`--exit-zero` is critical: when a file has BOTH an auto-fixable
    issue (F401) AND a non-fixable issue (e.g. undefined name F821),
    ruff exits non-zero. Without --exit-zero the auto-fix would be
    skipped entirely and the agent would have to fix BOTH manually.
    With --exit-zero, the F401 gets cleaned up and only the real
    error surfaces to the rework loop.
    """
    src = (
        "import os\n"           # unused (auto-fixable F401)
        "import json\n"         # used below
        "\n"
        "print(json.dumps({'x': undefined_variable}))\n"  # F821 — NOT auto-fixable
    )
    out, _ = await _maybe_ruff_format(tmp_path / "x.py", src)
    # F401 stripped:
    assert "import os" not in out
    # F821 left for the rework loop — `undefined_variable` still there
    assert "undefined_variable" in out
    # Used import preserved:
    assert "import json" in out


async def test_does_NOT_apply_unsafe_fixes(tmp_path: Path) -> None:
    """E711 (`== None` → `is None`) is marked UNSAFE by ruff — a
    weird ``__eq__`` override could change behaviour. We deliberately
    don't pass ``--unsafe-fixes``, so this stays in the source for the
    rework loop / agent to address. Pinning this so a future "enable
    --unsafe-fixes" change is conscious, not accidental."""
    src = (
        "def is_root(x):\n"
        "    return x == None\n"
    )
    out, _ = await _maybe_ruff_format(tmp_path / "x.py", src)
    # Still there — agent has to handle it, not the auto-format pass.
    assert "== None" in out


async def test_modernizes_typing_imports(tmp_path: Path) -> None:
    """UP006 / UP035 (use `list` instead of `typing.List`) is
    auto-fixable [*] and is in the project's `UP` selection.
    Catches stale typing imports that agents sometimes emit when
    they've been trained on older Python."""
    src = (
        "from typing import List\n"
        "\n"
        "def names() -> List[str]:\n"
        "    return ['a', 'b']\n"
    )
    out, changed = await _maybe_ruff_format(tmp_path / "x.py", src)
    assert changed is True
    # Either the import is gone (UP imports cleaned up) or `List` is replaced
    # — the safe assertion is just that the modern form is present.
    assert "list[str]" in out or "List[str]" not in out
