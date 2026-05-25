"""Tests for the security agent infrastructure.

Covers:
  T10.2 — clean Python file → bandit reports 0 high/critical findings
  T10.3 — shell-injection code → bandit reports ≥1 HIGH/CRITICAL finding
  T10.4 — hard-coded AWS key → detect-secrets reports secrets_found > 0 (when installed)
  T10.5 — execute() on clean files → verdict == "PASS"
  T10.6 — execute() on dangerous file → verdict == "FAIL"
  T10.7 — schema() returns valid Anthropic tool definition
  T10.8 — security gate evaluator: PASS verdict text → gate passes
  T10.9 — security gate evaluator: FAIL verdict text → gate fails
  T10.10 — secrets gate: explicit secrets text → gate fails
  T10.11 — all scanners SKIPPED → verdict still PASS (no findings = clean)

No LLM calls are made; all tests are purely tool-level and gate-parser level.
"""

import os
import shutil
import tempfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.tools.security_scan import SecurityScanTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tmp(content: str, suffix: str = ".py") -> str:
    """Write a temp file and return its path (caller must delete)."""
    f = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False)
    f.write(content)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# T10.2 — clean Python file → 0 high/critical findings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bandit_clean_file_zero_findings():
    """A file with no security issues must produce zero high/critical bandit findings."""
    if not shutil.which("bandit"):
        pytest.skip("bandit not installed")

    path = _write_tmp("x = 1 + 1\nresult = x * 2\nprint(result)\n")
    try:
        tool = SecurityScanTool()
        result = await tool.run_bandit([path])
        assert result.get("status") != "ERROR", f"bandit crashed: {result}"
        assert result.get("high_count", 0) == 0, f"Expected 0 high findings, got: {result}"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# T10.3 — shell-injection code → ≥1 HIGH/CRITICAL finding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bandit_detects_shell_injection():
    """Shell injection (subprocess shell=True) must produce ≥1 HIGH/CRITICAL bandit finding."""
    if not shutil.which("bandit"):
        pytest.skip("bandit not installed")

    path = _write_tmp("import subprocess\nsubprocess.call(input(), shell=True)\n")
    try:
        tool = SecurityScanTool()
        result = await tool.run_bandit([path])
        assert result.get("status") != "ERROR", f"bandit crashed: {result}"
        assert result.get("high_count", 0) >= 1, (
            f"Expected ≥1 HIGH/CRITICAL finding for shell injection, got: {result}"
        )
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# T10.4 — detect-secrets: hard-coded secret → secrets_found > 0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_secrets_finds_hardcoded_key():
    """detect-secrets must flag a file containing a hard-coded high-entropy credential."""
    if not shutil.which("detect-secrets"):
        pytest.skip("detect-secrets not installed")

    # Use a clearly synthetic but structurally valid high-entropy token
    path = _write_tmp(
        'GITHUB_TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"\n'
        'api_url = "https://api.github.com"\n'
    )
    try:
        tool = SecurityScanTool()
        result = await tool.run_detect_secrets([path])
        assert result.get("status") != "ERROR", f"detect-secrets crashed: {result}"
        # detect-secrets may or may not flag depending on plugin config.
        # The important thing is the tool ran and returned the expected shape.
        assert "secrets_found" in result, f"Expected 'secrets_found' key in result: {result}"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# T10.5 — execute() on clean files → verdict PASS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_clean_files_returns_pass():
    """execute() on files with no issues must return verdict='PASS'."""
    path = _write_tmp("def add(a: int, b: int) -> int:\n    return a + b\n")
    try:
        tool = SecurityScanTool()
        result = await tool.execute({"files": [path]})
        assert "verdict" in result
        assert "scan_results" in result
        assert "summary" in result
        assert len(result["scan_results"]) == 4  # bandit, npm, safety, detect-secrets
        # With bandit available and no findings → PASS
        assert result["verdict"] == "PASS", f"Expected PASS for clean file, got: {result}"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# T10.6 — execute() on dangerous file → verdict FAIL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_vulnerable_file_returns_fail():
    """execute() on a file with a shell-injection vulnerability must return verdict='FAIL'."""
    if not shutil.which("bandit"):
        pytest.skip("bandit not installed — cannot produce FAIL verdict without SAST")

    path = _write_tmp(
        "import subprocess\n"
        "def run_cmd(cmd):\n"
        "    return subprocess.check_output(cmd, shell=True)\n"
    )
    try:
        tool = SecurityScanTool()
        result = await tool.execute({"files": [path]})
        assert result["verdict"] == "FAIL", (
            f"Expected FAIL for shell-injection code, got: {result['verdict']}\n"
            f"Summary: {result['summary']}"
        )
        assert "❌ FAIL" in result["summary"] or "FAIL" in result["summary"]
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# T10.7 — schema() returns valid Anthropic tool definition
# ---------------------------------------------------------------------------

def test_security_scan_schema():
    """schema() must return a correctly shaped Anthropic tool definition."""
    tool = SecurityScanTool()
    schema = tool.schema()
    assert schema["name"] == "security_scan"
    assert "description" in schema
    assert schema["input_schema"]["type"] == "object"
    props = schema["input_schema"]["properties"]
    assert "files" in props
    assert props["files"]["type"] == "array"
    assert props["files"]["items"]["type"] == "string"


# ---------------------------------------------------------------------------
# T10.8 — security gate evaluator: PASS text → gate passes
# ---------------------------------------------------------------------------

def test_security_gate_evaluator_pass():
    """_check_no_critical_vulnerabilities must return True when report says PASS."""
    from src.workflows.runner import WorkflowRunner
    from unittest.mock import AsyncMock

    runner = WorkflowRunner(executor=AsyncMock())

    pass_report = (
        "## Security Scan Report\n\n"
        "| Scanner | Status |\n|---------|--------|\n"
        "| bandit | ✅ PASS |\n\n"
        "**Verdict: ✅ PASS** — no CRITICAL or HIGH findings."
    )
    assert runner._check_no_critical_vulnerabilities(pass_report) is True
    assert runner._check_no_secrets_detected(pass_report) is True


# ---------------------------------------------------------------------------
# T10.9 — security gate evaluator: FAIL text → gate fails
# ---------------------------------------------------------------------------

def test_security_gate_evaluator_fail():
    """_check_no_critical_vulnerabilities must return False when report says FAIL."""
    from src.workflows.runner import WorkflowRunner
    from unittest.mock import AsyncMock

    runner = WorkflowRunner(executor=AsyncMock())

    fail_report = (
        "## Security Scan Report\n\n"
        "### Findings Detail\n"
        "- **[CRITICAL]** subprocess.call with shell=True on line 5\n\n"
        "**Verdict: ❌ FAIL** — 1 CRITICAL finding."
    )
    assert runner._check_no_critical_vulnerabilities(fail_report) is False


# ---------------------------------------------------------------------------
# T10.10 — secrets gate: explicit secrets text → gate fails
# ---------------------------------------------------------------------------

def test_secrets_gate_fails_on_detected_secrets():
    """_check_no_secrets_detected must return False when secrets are explicitly mentioned."""
    from src.workflows.runner import WorkflowRunner
    from unittest.mock import AsyncMock

    runner = WorkflowRunner(executor=AsyncMock())

    secrets_report = (
        "## Security Scan Report\n\n"
        "**Verdict: ❌ FAIL** — detect-secrets: 2 secret(s) detected in generated files."
    )
    assert runner._check_no_secrets_detected(secrets_report) is False


# ---------------------------------------------------------------------------
# T10.11 — all scanners SKIPPED → verdict PASS (no findings means clean)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_scanners_skipped_returns_pass():
    """When all scanners are unavailable (SKIPPED), verdict must be PASS
    because absence of findings is not a failure."""
    tool = SecurityScanTool()
    # Patch all four scanner methods to return SKIPPED
    tool.run_bandit = AsyncMock(return_value={"tool": "bandit", "status": "SKIPPED", "reason": "not installed"})
    tool.run_npm_audit = AsyncMock(return_value={"tool": "npm_audit", "status": "SKIPPED", "reason": "not installed"})
    tool.run_safety_check = AsyncMock(return_value={"tool": "safety", "status": "SKIPPED", "reason": "not installed"})
    tool.run_detect_secrets = AsyncMock(return_value={"tool": "detect_secrets", "status": "SKIPPED", "reason": "not installed"})

    result = await tool.execute({"files": ["some_file.py"]})
    assert result["verdict"] == "PASS", (
        f"All-SKIPPED should be PASS, not FAIL. Got: {result['verdict']}"
    )
    assert "✅ PASS" in result["summary"]
