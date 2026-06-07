"""Phase AE-5 smoke test for architecture_reviewer (AET-36).

Replays the AET-33 audit corpus (5 real historical subtasks + 3
synthetic recall probes) as structured finding lists and verifies:

  1. THE TUNING (AET-34) LANDED. The system prompt at
     config/agents/architecture_reviewer.yaml contains the trivial-
     change fast path, the page-vs-component rule, the anti-pattern
     list, and the CRITICAL/HIGH severity split the audit
     (docs/arch-reviewer-audit.md) recommended. A future edit that
     removes any of these would fail this test loudly rather than
     silently regress the agent.

  2. THE THRESHOLD (AET-35) GATES CORRECTLY. The
     `arch_review_block_severity` threshold helper produces the
     right BLOCK/PASS decisions across both supported cutoffs
     ('critical' default, 'high' tighter), for every shape of
     finding the agent emits under the new prompt.

  3. THE AUDIT CORPUS REPLAYS CLEAN. For each of the 5 historical
     subtasks (ACR-01..03 are the 5 real rows, collapsed by case
     class) and the 3 synthetic recall probes (ACR-04..06), the
     classified ground-truth verdict from the audit matches the
     gate's decision under the default threshold. This is the
     replay contract — it locks in the audit's findings as
     executable assertions.

Setup
-----
No real agent invocation. We don't have an LLM in CI and the goal
of AET-36 (per its task spec) is to verify that previous false
positives no longer block and previously-missed issues now surface
— that's a CONTRACT test, not a behaviour test. We synthesize the
finding list each historical / probe case would produce under the
new prompt and run it through the AET-35 helper.

Run via:
  docker compose exec backend pytest tests/test_arch_reviewer_smoke.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core.security_threshold import (
    ARCH_DEFAULT_SEVERITY,
    get_arch_block_severity,
    is_blocking,
    split_findings,
)


# ── 1 · System prompt content guards (AET-34 tuning landed) ───────────────


_AGENT_YAML = Path(__file__).resolve().parents[1] / (
    "config/agents/architecture_reviewer.yaml"
)


@pytest.fixture(scope="module")
def agent_prompt() -> str:
    data = yaml.safe_load(_AGENT_YAML.read_text(encoding="utf-8"))
    prompt = data.get("system_prompt", "")
    assert prompt, "architecture_reviewer.yaml has no system_prompt"
    return prompt


def test_prompt_has_trivial_change_fast_path(agent_prompt: str):
    """Audit R1 — non-source-only cycles must emit a ≤600-char report."""
    assert "TRIVIAL-CHANGE FAST PATH" in agent_prompt
    # The size budget is the contract that prevents the ceremonial 2KB
    # output on a 1-line requirements.txt change (audit §4.3).
    assert "≤600 chars" in agent_prompt or "<=600 chars" in agent_prompt


def test_prompt_has_page_vs_component_definition(agent_prompt: str):
    """Audit R2 — rule 3 must apply ONLY to files under
    frontend/src/pages/, never to utility components."""
    assert "PAGE vs COMPONENT" in agent_prompt
    assert "frontend/src/pages/*.tsx" in agent_prompt
    # The "would this URL appear in the address bar?" heuristic is the
    # operator-friendly check that codifies what subtask #4 did right.
    assert "address bar" in agent_prompt.lower()


def test_prompt_has_critical_high_severity_split(agent_prompt: str):
    """Audit R3 — rules 1-3 produce CRITICAL (block), rules 4-6
    produce HIGH (annotate-only). The threshold (AET-35) reads this."""
    assert "rules 1, 2, 3 are STRUCTURAL" in agent_prompt
    assert "CRITICAL" in agent_prompt and "ARCH_VIOLATION" in agent_prompt
    assert "Rules 4, 5, 6 are QUALITY signals" in agent_prompt
    assert "do NOT block" in agent_prompt


def test_prompt_has_anti_pattern_list(agent_prompt: str):
    """Audit R5 — explicit DO-NOT-FLAG categories that are
    historically false-positive traps."""
    assert "ANTI-PATTERN LIST" in agent_prompt
    # Spot-check three categories the audit named:
    assert "Comments above an import" in agent_prompt
    assert "Type aliases" in agent_prompt
    assert "Lock files" in agent_prompt


def test_prompt_references_aet34_aet35(agent_prompt: str):
    """Tuning was tagged with its AE task IDs so future archaeology
    (which lesson came from which audit?) is one grep away."""
    assert "AET-34" in agent_prompt
    assert "AET-35" in agent_prompt


# ── 2 · AET-35 threshold helper behaviour ─────────────────────────────────


def test_arch_threshold_defaults_to_critical():
    """Audit R3 — gate should default to blocking ONLY on CRITICAL,
    matching the agent's own ARCH_VIOLATION verdict logic."""
    assert ARCH_DEFAULT_SEVERITY == "critical"
    assert get_arch_block_severity(None) == "critical"
    assert get_arch_block_severity({}) == "critical"
    # Bogus values fall back rather than disabling the gate silently.
    assert get_arch_block_severity({
        "thresholds": {"arch_review_block_severity": {"value": "bogus"}},
    }) == "critical"
    # 'medium' and 'low' are below the lowest arch severity (HIGH) so
    # they're also rejected — only the two supported values land.
    assert get_arch_block_severity({
        "thresholds": {"arch_review_block_severity": {"value": "medium"}},
    }) == "critical"


def test_arch_threshold_override_to_high():
    """Operators who want HIGH findings to gate too set 'high'."""
    assert get_arch_block_severity({
        "thresholds": {"arch_review_block_severity": {"value": "high"}},
    }) == "high"


def test_is_blocking_matrix_for_arch_severities():
    """The two arch severities under both supported cutoffs."""
    # Default cutoff: blocks ONLY on CRITICAL.
    assert is_blocking("critical", "critical") is True
    assert is_blocking("high", "critical") is False

    # Tightened cutoff: blocks on CRITICAL AND HIGH.
    assert is_blocking("critical", "high") is True
    assert is_blocking("high", "high") is True

    # Unknown severities never accidentally block.
    assert is_blocking("", "critical") is False
    assert is_blocking("nonsense", "high") is False


# ── 3 · Audit-corpus replay (executable form of the audit's table) ────────


# Each case bundles:
#   - description (what the historical subtask reviewed)
#   - findings    (what the agent would emit under the new prompt)
#   - expected_at_critical_cutoff (the AET-35 default gate decision)
#   - expected_at_high_cutoff     (tighter operator override)
#   - source     (audit subtask id, or "synthetic" for recall probes)
_AUDIT_CASES: list[dict[str, Any]] = [
    {
        "id": "ACR-01",
        "description": (
            "Pure no-op rework cycle (lesson #3 path)"
        ),
        "findings": [],
        "expected_at_critical": "APPROVED",
        "expected_at_high":     "APPROVED",
        "source": "subtask …c194",
    },
    {
        "id": "ACR-02",
        "description": (
            "Infrastructure-only — Dockerfile + 1-line requirements.txt"
        ),
        "findings": [],  # trivial-change fast path → zero findings
        "expected_at_critical": "APPROVED",
        "expected_at_high":     "APPROVED",
        "source": "subtask …f373 / …4292",
    },
    {
        "id": "ACR-03",
        "description": (
            "Frontend utility component added (NOT a page); inline "
            "import in App.tsx"
        ),
        "findings": [],  # correctly distinguished page-vs-component
        "expected_at_critical": "APPROVED",
        "expected_at_high":     "APPROVED",
        "source": "subtask …4ebf",
    },
    {
        "id": "ACR-04",
        "description": (
            "RECALL PROBE — new page added under frontend/src/pages/ "
            "WITHOUT a <Route> entry in App.tsx"
        ),
        "findings": [
            {
                "rule_id": "rule-3-frontend-router",
                "severity": "critical",
                "file": "frontend/src/pages/NewDashboard.tsx",
                "line": 0,
                "message": (
                    "NewDashboard page exists under frontend/src/pages/ "
                    "but has no <Route> entry in App.tsx — page is "
                    "unreachable"
                ),
            },
        ],
        "expected_at_critical": "ARCH_VIOLATION",
        "expected_at_high":     "ARCH_VIOLATION",
        "source": "synthetic",
    },
    {
        "id": "ACR-05",
        "description": (
            "RECALL PROBE — route file imports aiosqlite directly, "
            "bypassing StateStore"
        ),
        "findings": [
            {
                "rule_id": "rule-1-layer-boundary",
                "severity": "critical",
                "file": "src/api/routes/widgets.py",
                "line": 3,
                "message": (
                    "import aiosqlite — DB access in route layer "
                    "bypasses StateStore"
                ),
            },
        ],
        "expected_at_critical": "ARCH_VIOLATION",
        "expected_at_high":     "ARCH_VIOLATION",
        "source": "synthetic",
    },
    {
        "id": "ACR-06",
        "description": (
            "Pydantic model uses @validator (v1 pattern) — quality "
            "signal, not breakage"
        ),
        "findings": [
            {
                "rule_id": "rule-4-pydantic-v2",
                "severity": "high",
                "file": "src/models/widget.py",
                "line": 12,
                "message": "@validator is Pydantic v1; use @field_validator",
            },
        ],
        # Default cutoff = critical → HIGH doesn't block. Tightened
        # cutoff = high → HIGH blocks. This case is the WHOLE point
        # of the AET-35 threshold (it's the dial operators turn).
        "expected_at_critical": "APPROVED",
        "expected_at_high":     "ARCH_VIOLATION",
        "source": "synthetic",
    },
]


@pytest.mark.parametrize("case", _AUDIT_CASES, ids=lambda c: c["id"])
def test_audit_corpus_replay_at_default_cutoff(case: dict[str, Any]):
    """Each audit corpus case + recall probe must produce the verdict
    the audit predicted under the AET-35 default cutoff ('critical')."""
    blocking, _ = split_findings(case["findings"], "critical")
    verdict = "ARCH_VIOLATION" if blocking else "APPROVED"
    assert verdict == case["expected_at_critical"], (
        f"{case['id']} ({case['description']}): "
        f"expected {case['expected_at_critical']!r} under default cutoff, "
        f"got {verdict!r} (blocking={blocking})"
    )


@pytest.mark.parametrize("case", _AUDIT_CASES, ids=lambda c: c["id"])
def test_audit_corpus_replay_at_high_cutoff(case: dict[str, Any]):
    """Same cases under the tightened 'high' cutoff. Only ACR-06
    flips verdict; all others stay the same."""
    blocking, _ = split_findings(case["findings"], "high")
    verdict = "ARCH_VIOLATION" if blocking else "APPROVED"
    assert verdict == case["expected_at_high"], (
        f"{case['id']} ({case['description']}): "
        f"expected {case['expected_at_high']!r} under high cutoff, "
        f"got {verdict!r} (blocking={blocking})"
    )


def test_threshold_provides_meaningful_difference():
    """At least one case in the corpus MUST produce different verdicts
    under the two cutoffs — otherwise the threshold is decorative,
    not load-bearing. This catches a future regression where every
    finding got bumped to CRITICAL and the threshold became dead code."""
    differs = [
        c for c in _AUDIT_CASES
        if c["expected_at_critical"] != c["expected_at_high"]
    ]
    assert differs, (
        "no audit case differentiates between critical/high cutoffs — "
        "AET-35 threshold has no observable effect, which means either "
        "the corpus is too narrow or the severity-emission logic in "
        "AET-34's prompt drifted. Re-audit and add a HIGH-only case."
    )
    # The audit predicted ACR-06 (Pydantic v1) is the differentiator.
    assert any(c["id"] == "ACR-06" for c in differs)
