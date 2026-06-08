"""HAI-08 — tool manifest loader: parse, role-filter, register."""

import textwrap

from manifest import ToolSpec, load_manifest, register_tools, role_allows, tools_for_role


def test_load_manifest(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        textwrap.dedent(
            """
            tools:
              - name: monitor_x
                description: read x
                min_role: viewer
                method: get
                path: /api/v1/x
              - name: deploy_y
                min_role: admin
                method: POST
                path: /api/v1/y
            """
        ),
        encoding="utf-8",
    )
    specs = load_manifest(p)
    assert [s.name for s in specs] == ["monitor_x", "deploy_y"]
    assert specs[0].min_role == "viewer"
    assert specs[0].method == "GET"          # normalized upper
    assert specs[1].description == ""         # default when omitted


def test_role_allows_hierarchy():
    assert role_allows("admin", "viewer") is True
    assert role_allows("admin", "admin") is True
    assert role_allows("developer", "admin") is False
    assert role_allows("viewer", "developer") is False
    assert role_allows("viewer", "viewer") is True
    assert role_allows(None, "viewer") is False   # unresolved role → nothing


def test_tools_for_role_filters():
    specs = [
        ToolSpec("a", "", "viewer", "GET", "/a"),
        ToolSpec("b", "", "developer", "GET", "/b"),
        ToolSpec("c", "", "admin", "POST", "/c"),
    ]
    assert {s.name for s in tools_for_role(specs, "viewer")} == {"a"}
    assert {s.name for s in tools_for_role(specs, "developer")} == {"a", "b"}
    assert {s.name for s in tools_for_role(specs, "admin")} == {"a", "b", "c"}
    assert tools_for_role(specs, None) == []


def test_register_tools_respects_role_and_impl():
    specs = [
        ToolSpec("a", "tool a", "viewer", "GET", "/a"),
        ToolSpec("b", "tool b", "admin", "POST", "/b"),
        ToolSpec("c", "no impl", "viewer", "GET", "/c"),
    ]
    impls = {"a": (lambda: None), "b": (lambda: None)}  # 'c' intentionally has no impl

    class _MCP:
        def __init__(self):
            self.added: list[tuple[str, str]] = []

        def add_tool(self, fn, name=None, description=None):
            self.added.append((name, description))

    m = _MCP()
    # viewer → only 'a' ('b' needs admin; 'c' has no impl)
    assert register_tools(m, specs, "viewer", impls) == ["a"]
    assert m.added == [("a", "tool a")]

    m2 = _MCP()
    # admin → 'a' and 'b' ('c' still skipped — no impl)
    assert register_tools(m2, specs, "admin", impls) == ["a", "b"]


def test_per_tier_identities_see_lifecycle_companions():
    """HAI-43/44 — the lifecycle read companions are viewer-tier, so BOTH a
    hermes-monitor (viewer) and a hermes-operator (developer) identity get them.
    Loaded from the real shipped manifest, not a fixture."""
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parents[1] / "tools_manifest.yaml"
    specs = load_manifest(manifest_path)
    companions = {"project_get_prd", "project_get_apispec", "project_get_buildplan", "project_get_tasks"}
    for spec in specs:
        if spec.name in companions:
            assert spec.min_role == "viewer", f"{spec.name} should be viewer-tier"
    viewer_names = {s.name for s in tools_for_role(specs, "viewer")}
    developer_names = {s.name for s in tools_for_role(specs, "developer")}
    assert companions <= viewer_names        # hermes-monitor (viewer) sees them
    assert companions <= developer_names     # hermes-operator (developer) too
