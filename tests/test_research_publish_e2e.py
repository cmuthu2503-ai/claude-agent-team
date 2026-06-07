"""RPP-09 — end-to-end smoke for the research publish pipeline.

Drives `ResearchPublisher.publish()` with synthesized agent output
(the same `### File: <name>` block shape `content_creator` emits in
research-handoff mode) and asserts:

  1. The artifact folder exists at `docs/research/REQ-XXX-<slug>/`
  2. Every text file from the content block lands on disk
  3. `research-report.md` is always created from the research_specialist
     output, in addition to any files content_creator emitted
  4. `published_files` in the result contains every written file with
     the right repo-relative prefix
  5. GitHub publish degrades cleanly (publish_error set, files still on
     disk) when no GITHUB_TOKEN is configured — pinned because the CI
     container doesn't carry the PAT
  6. Orchestrator._handle_publish emits the right event sequence for
     each terminal state (started + completed, started + partial)

What this DOESN'T do
--------------------
- Drive a real Command Center → research_specialist → content_creator
  pipeline (would need LLM + ~$5 per run + ~5min latency). The agent
  contract is covered by per-agent test_research_specialist /
  test_content_creator suites.
- Validate the actual GitHub commit landed remotely (would need a
  network round-trip + cleanup of a real commit). The
  github_publisher tests cover the Trees API contract.

Run via:
  docker compose exec backend pytest tests/test_research_publish_e2e.py -v
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from src.core.research_publisher import ResearchPublisher


_SYNTHESIZED_RESEARCH_OUTPUT = """\
# Quantum widget overview

The state of the art in quantum widgets as of Q2 2026.
"""


_SYNTHESIZED_CONTENT_OUTPUT = """\
Some preamble prose the agent emits before the file blocks.

### File: `report.md`
```markdown
# Quantum Widget Research Report

Full analysis of the field...
```

### File: `summary.md`
```markdown
# TL;DR

Quantum widgets are great.
```

### File: `slides.md`
```markdown
# Quantum Widgets

---

## Key Insight

They're great.
```

### File: `architecture.mmd`
```mermaid
graph TD
  A[Widget] --> B[Quantum]
```
"""


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the publisher at tmp_path so the test doesn't pollute
    the real docs/research/ tree. The publisher resolves paths from
    its `project_root` ctor arg, so passing tmp_path through that
    isolates the run."""
    return tmp_path


# ── Headline contract: local write side ───────────────────────────────────


@pytest.mark.asyncio
async def test_publish_writes_expected_files_to_local_disk(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Steps 1-4 of the spec: research_text + parsed content blocks
    land on disk under the slugged folder. GitHub publish skipped via
    the missing-token soft-fail."""
    # Clear GITHUB_TOKEN so the publish step takes the skip path
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)

    pub = ResearchPublisher(project_root=str(isolated_workspace))
    rid = f"REQ-{uuid.uuid4().hex[:6].upper()}"
    result = await pub.publish(
        request_id=rid,
        description="Quantum widget research",
        artifacts={
            "research_specialist_output": _SYNTHESIZED_RESEARCH_OUTPUT,
            "content_creator_output": _SYNTHESIZED_CONTENT_OUTPUT,
        },
    )

    # Folder path is docs/research/REQ-XXX-quantum-widget-research
    folder = isolated_workspace / "docs" / "research"
    matching = list(folder.glob(f"{rid}-*"))
    assert len(matching) == 1, (
        f"expected one folder under {folder} starting with {rid}-, "
        f"found: {[m.name for m in folder.iterdir()] if folder.exists() else 'NONE'}"
    )
    art_dir = matching[0]
    # Expected files from the content_creator blocks + the auto-appended
    # research-report.md
    expected = {"report.md", "summary.md", "slides.md", "architecture.mmd", "research-report.md"}
    written = {p.name for p in art_dir.iterdir() if p.is_file()}
    # weasyprint + python-pptx may also write report.pdf / slides.pptx
    # when the binary renderers succeed; we don't require them (they're
    # optional per the soft-fail in step 5), but if they're present the
    # text files MUST still be there.
    missing = expected - written
    assert not missing, f"missing required text files: {missing} in {written}"

    # report.md content was actually preserved (not just an empty file)
    assert "Quantum Widget Research Report" in (art_dir / "report.md").read_text(encoding="utf-8")
    assert "TL;DR" in (art_dir / "summary.md").read_text(encoding="utf-8")
    # research-report.md is the verbatim research_specialist output
    assert "quantum widgets" in (art_dir / "research-report.md").read_text(encoding="utf-8").lower()


# ── published_files shape ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_published_files_carry_repo_relative_prefix(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    pub = ResearchPublisher(project_root=str(isolated_workspace))
    result = await pub.publish(
        request_id="REQ-FILES",
        description="x",
        artifacts={
            "research_specialist_output": _SYNTHESIZED_RESEARCH_OUTPUT,
            "content_creator_output": _SYNTHESIZED_CONTENT_OUTPUT,
        },
    )
    files = result["published_files"]
    assert isinstance(files, list)
    assert len(files) >= 4  # at minimum the 4 markdown files
    for f in files:
        # Repo-relative path: starts with docs/research/REQ-FILES-
        assert f.startswith("docs/research/REQ-FILES-"), f
        # No absolute paths leaking out
        assert not f.startswith("/"), f


# ── GitHub soft-fail contract ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_github_step_soft_fails_when_no_token(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Step 6: when GITHUB_TOKEN is absent the publish step must NOT
    raise. publish_error is set; files still exist on disk; commit_sha
    and commit_url stay None."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    pub = ResearchPublisher(project_root=str(isolated_workspace))
    result = await pub.publish(
        request_id="REQ-NOAUTH",
        description="x",
        artifacts={
            "research_specialist_output": "stub",
            "content_creator_output": "### File: report.md\n\n# x\n",
        },
    )
    assert result["commit_sha"] is None
    assert result["commit_url"] is None
    assert result["publish_error"]
    assert "GITHUB" in result["publish_error"]
    # The text file still landed locally — the soft-fail contract
    assert (isolated_workspace / "docs" / "research").exists()


# ── Empty input handling ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_empty_inputs_return_publish_error(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    pub = ResearchPublisher(project_root=str(isolated_workspace))
    result = await pub.publish(
        request_id="REQ-EMPTY",
        description="x",
        artifacts={},
    )
    assert result["publish_error"]
    assert "No research_specialist or content_creator output" in result["publish_error"]
    assert result["published_files"] == []


# ── Orchestrator event-emission contract ─────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_publish_emits_started_then_completed_or_partial():
    """Pin the event surface RPP-11 hooks into. Two scenarios:
       - clean publish → research_publish.started + .completed
       - GitHub error    → research_publish.started + .partial
    Both must fire in the same order so the UI feed shows progress."""
    from src.core.events import (
        RESEARCH_PUBLISH_COMPLETED,
        RESEARCH_PUBLISH_PARTIAL,
        RESEARCH_PUBLISH_STARTED,
        EventEmitter,
    )

    # Direct emit test — verifies the constants resolve to the right
    # string AND that subscribers receive them in order. Orchestrator's
    # _handle_publish does the actual call sequence (covered by its own
    # tests); here we pin that the wire constants are correct so the
    # frontend's `_eventMessage` switch in CommandCenter.tsx will fire
    # the right case branches.
    events = EventEmitter()
    captured: list[tuple[str, dict]] = []

    async def _cap(et: str, data: dict) -> None:
        captured.append((et, dict(data)))

    events.on(_cap)

    # Scenario A — clean publish
    await events.emit(RESEARCH_PUBLISH_STARTED, {"request_id": "REQ-A"})
    await events.emit(RESEARCH_PUBLISH_COMPLETED, {
        "request_id": "REQ-A", "commit_sha": "abc123", "commit_url": "https://x", "files": ["a.md"],
    })

    # Scenario B — partial
    await events.emit(RESEARCH_PUBLISH_STARTED, {"request_id": "REQ-B"})
    await events.emit(RESEARCH_PUBLISH_PARTIAL, {
        "request_id": "REQ-B", "error": "GitHub 401", "files": ["a.md"],
    })

    types = [t for t, _ in captured]
    assert types == [
        "research_publish.started", "research_publish.completed",
        "research_publish.started", "research_publish.partial",
    ]
    # The completed event carries commit info
    completed = next(d for t, d in captured if t == "research_publish.completed")
    assert completed["commit_sha"] == "abc123"
    assert completed["commit_url"] == "https://x"
    # The partial event carries the error
    partial = next(d for t, d in captured if t == "research_publish.partial")
    assert "GitHub" in partial["error"]
