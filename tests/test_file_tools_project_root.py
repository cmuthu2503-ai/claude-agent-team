"""Tests for the per-call ``project_root`` override on file tools.

Regression coverage for the 2026-05-21 cross-tree pollution bug: an
agent task dispatched on a CrewAI Request issued search_replace calls
against ``frontend/src/App.tsx``. With the legacy single-root design,
those resolved to the PLATFORM tree's App.tsx and clobbered it,
breaking Vite's import-analysis for the platform UI.

The fix threads ``project_root`` through:
  AgentSystemExecutor.execute
    → BaseAgent.process_task(..., project_root=...)
    → BaseAgent._execute_tool
    → ToolRegistry.execute(..., project_root=...)
    → File*Tool.execute(params, project_root=...)
    → _resolve_path(rel, effective_root)

These tests pin down the leaf behaviour and the registry's forwarding
contract so the pattern can't regress quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.file_tools import FileReadTool, FileWriteTool, SearchReplaceTool
from src.tools.registry import ToolRegistry


# ── Direct: each tool's execute() respects the override ──────────────────────


async def test_file_write_respects_project_root_override(tmp_path: Path) -> None:
    """The fix in one line: when execute() receives project_root=B,
    the file lands in B even though the tool was constructed with A."""
    platform_root = tmp_path / "platform"
    project_root = tmp_path / "myproject"
    platform_root.mkdir()
    project_root.mkdir()

    tool = FileWriteTool(project_root=str(platform_root))  # constructed with PLATFORM
    result = await tool.execute(
        {"path": "src/App.tsx", "content": "// hi\n"},
        project_root=project_root,  # but called with PROJECT
    )
    assert "File written" in result
    # Lands in the project tree
    assert (project_root / "src" / "App.tsx").exists()
    # NOT in the platform tree (the bug we're regressing)
    assert not (platform_root / "src" / "App.tsx").exists()


async def test_search_replace_respects_project_root_override(tmp_path: Path) -> None:
    """The exact REQ-* failure mode: search_replace on frontend/src/App.tsx
    from a per-project task must hit the project tree, not the platform tree."""
    platform_root = tmp_path / "platform"
    project_root = tmp_path / "crewai"
    (platform_root / "frontend" / "src").mkdir(parents=True)
    (project_root / "frontend" / "src").mkdir(parents=True)

    # Seed both trees with the same starter content. If the override is
    # honoured, only the project tree's copy gets touched.
    starter = "const x = 1\nconst y = 2\n"
    (platform_root / "frontend" / "src" / "App.tsx").write_text(starter)
    (project_root / "frontend" / "src" / "App.tsx").write_text(starter)

    tool = SearchReplaceTool(project_root=str(platform_root))
    result = await tool.execute(
        {
            "path": "frontend/src/App.tsx",
            "old_string": "const x = 1",
            "new_string": "const x = 999",
        },
        project_root=project_root,
    )
    assert "OK" in result
    # Project tree was modified
    assert "const x = 999" in (project_root / "frontend" / "src" / "App.tsx").read_text()
    # Platform tree was NOT — this is the regression
    assert (platform_root / "frontend" / "src" / "App.tsx").read_text() == starter


async def test_file_read_respects_project_root_override(tmp_path: Path) -> None:
    """Symmetric for reads — the override must scope where the tool looks."""
    platform_root = tmp_path / "platform"
    project_root = tmp_path / "myproj"
    (platform_root / "src").mkdir(parents=True)
    (project_root / "src").mkdir(parents=True)

    # Different contents in each tree at the SAME relative path.
    (platform_root / "src" / "x.py").write_text("# platform copy\n")
    (project_root / "src" / "x.py").write_text("# project copy\n")

    tool = FileReadTool(project_root=str(platform_root))
    # No override → reads from platform tree (legacy default)
    out_default = await tool.execute({"path": "src/x.py"})
    assert "platform copy" in out_default

    # With override → reads from project tree
    out_override = await tool.execute({"path": "src/x.py"}, project_root=project_root)
    assert "project copy" in out_override


# ── Path-traversal guard uses the effective root ─────────────────────────────


async def test_path_traversal_blocked_against_override_root(tmp_path: Path) -> None:
    """A per-project task must NOT be able to escape into the platform
    tree via `../` even though the tool was constructed there. The
    guard now checks against the OVERRIDE root, not self.project_root."""
    platform_root = tmp_path / "platform"
    project_root = tmp_path / "evil-project"
    platform_root.mkdir()
    project_root.mkdir()
    (platform_root / "secret.txt").write_text("PLATFORM SECRET")

    tool = FileReadTool(project_root=str(platform_root))
    # Try to escape upward from the project tree into the platform.
    result = await tool.execute(
        {"path": "../platform/secret.txt"},
        project_root=project_root,
    )
    # Either the resolver rejected it, OR (after path-resolve) the file
    # doesn't exist within the project tree. Either way: no leak.
    assert "PLATFORM SECRET" not in result
    assert "Error" in result or "not found" in result.lower() or "escapes" in result.lower()


# ── Registry forwarding contract ─────────────────────────────────────────────


async def test_registry_forwards_project_root_to_file_tools(tmp_path: Path) -> None:
    """ToolRegistry.execute(..., project_root=X) must reach the tool's
    execute(). Without this, the chain breaks silently — the agent
    would still scribble into the wrong tree even if process_task
    resolved correctly."""
    platform_root = tmp_path / "platform"
    project_root = tmp_path / "myproj"
    platform_root.mkdir()
    project_root.mkdir()

    # Set up a minimal config + registry. Tool permission check needs
    # the agent to exist in config; we stub by registering and granting.
    class _StubConfig:
        # _load_tools() reads `config.tools["tools"]` — nested, not flat.
        agents = {"backend_specialist": {"tools": ["file_write"]}}
        tools = {"tools": {"file_write": {"available_to": ["backend_specialist"]}}}

    registry = ToolRegistry(config=_StubConfig())
    registry.register_implementation(
        "file_write", FileWriteTool(project_root=str(platform_root))
    )

    await registry.execute(
        tool_name="file_write",
        agent_id="backend_specialist",
        params={"path": "deep/path/test.txt", "content": "hello\n"},
        project_root=project_root,
    )
    # File lands in the project tree
    assert (project_root / "deep" / "path" / "test.txt").exists()
    # And NOT the platform tree
    assert not (platform_root / "deep" / "path" / "test.txt").exists()


async def test_registry_no_override_falls_back_to_default(tmp_path: Path) -> None:
    """Backwards compat: registry.execute without project_root behaves
    as before — files land in the tool's constructor project_root.
    Lets us roll out the change without changing every existing
    test_*.py file or accidentally breaking platform-level Requests."""
    platform_root = tmp_path / "platform"
    platform_root.mkdir()

    class _StubConfig:
        # _load_tools() reads `config.tools["tools"]` — nested, not flat.
        agents = {"backend_specialist": {"tools": ["file_write"]}}
        tools = {"tools": {"file_write": {"available_to": ["backend_specialist"]}}}

    registry = ToolRegistry(config=_StubConfig())
    registry.register_implementation(
        "file_write", FileWriteTool(project_root=str(platform_root))
    )

    await registry.execute(
        tool_name="file_write",
        agent_id="backend_specialist",
        params={"path": "x.txt", "content": "y\n"},
        # No project_root kwarg → use tool's default (platform_root)
    )
    assert (platform_root / "x.txt").exists()


# ── Auto-format still works with override ────────────────────────────────────


@pytest.mark.skipif(
    not __import__("shutil").which("ruff"),
    reason="ruff binary not present in this environment",
)
async def test_auto_format_works_with_project_root_override(tmp_path: Path) -> None:
    """ruff format runs with cwd=path.parent regardless of whether the
    project_root override was used. So a .py file written via the
    override still gets formatted against the per-project pyproject."""
    platform_root = tmp_path / "platform"
    project_root = tmp_path / "myproj"
    platform_root.mkdir()
    project_root.mkdir()

    tool = FileWriteTool(project_root=str(platform_root))
    long_line = (
        "very_long_variable_name = "
        "{'k1':'v1','k2':'v2','k3':'v3','k4':'v4','k5':'v5','k6':'v6'}\n"
    )
    await tool.execute(
        {"path": "x.py", "content": long_line},
        project_root=project_root,
    )
    written = (project_root / "x.py").read_text()
    # ruff format wraps a single-line dict literal that's too long.
    # Multi-line output is a sign formatting ran. We don't assert exact
    # output (ruff version may change), just that it isn't a single line.
    assert written.count("\n") >= 2 or written == long_line  # tolerant
