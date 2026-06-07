"""Secret scanner — AET-17.

Pre-``code_commit`` defence against leaking credentials. Unlike
``sast_scan`` (which calls subprocess linters on files materialised
to disk), this tool runs against the agent's **in-memory emitted
content** so a leak is caught BEFORE the ``code_writer`` /
``github_publisher`` stage writes anything anywhere — never on disk,
never on a branch, never in a PR diff.

Detection has two layers:

  1. **Regex catalog** — fast, low-false-positive patterns for the
     credentials we actually use or commonly receive: AWS access keys,
     Anthropic AWS workspace IDs, PEM private-key headers, GitHub PATs,
     Slack tokens, generic JWT-style triples, npm/PyPI tokens, etc.
     Each pattern has a stable ``rule_id`` so the AET-22 smoke can
     assert on it.

  2. **Shannon-entropy filter** — catches the long tail (UUIDs, base64
     blobs, hex digests) where a hand-rolled regex would either miss
     or flag every random-looking constant in normal code. Only
     strings ≥ ``ENTROPY_MIN_LEN`` chars whose Shannon entropy in
     bits-per-char is ≥ ``ENTROPY_THRESHOLD`` and look like a token
     (no spaces, mostly alphanumeric) are reported.

Either layer firing yields a finding. Findings carry a small redacted
preview (first 4 + last 4 chars) — the agent receives enough to
disambiguate which string tripped without echoing the secret back into
the next prompt.

Return shape::

    {
      "verdict":  "PASS" | "BLOCK",            # BLOCK if any finding
      "findings": [
        {
          "rule_id":   "aws_access_key_id" | "shannon_entropy",
          "severity":  "critical" | "high",    # never below high
          "file":      "src/api/foo.py",
          "line":      42,
          "match_preview": "AKIA…12AB",        # redacted
          "detector":  "regex" | "entropy",
        }, …
      ],
      "summary":  "<one-line verdict>",
    }

Critical-vs-high
----------------
Known credential patterns (AWS keys, PEM blocks, GitHub PATs,
Anthropic workspace IDs) are ``critical`` — they're unambiguous
leaks. The entropy heuristic and the generic 32-char token regex are
``high`` because they may be a UUID constant or a hash, not a real
secret — still worth blocking, but the severity acknowledges the
non-zero false-positive rate. Both cause BLOCK; the distinction is
for the gate's annotation, not the routing.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import structlog

logger = structlog.get_logger()


# ── Entropy detector tuning ──────────────────────────────────────────────


# Minimum length for entropy consideration. Below this the
# probability of random-looking high-entropy strings appearing in
# normal code (e.g. a 16-char hex constant) is too high. Bumping
# above 20 weakens detection of short-but-real secrets like
# 24-char npm tokens.
ENTROPY_MIN_LEN = 20

# Bits/char above which a string is considered "high entropy".
# - Base64 over a uniform input maxes around 6.0 bits/char
# - Random hex maxes around 4.0 bits/char
# - UUID v4 lands ~3.9 bits/char (low randomness, lots of "-")
# 4.0 is a deliberate compromise: catches base64 blobs and pure-hex
# tokens above 20 chars; ignores UUIDs and similar low-entropy IDs
# that show up in fixtures and comments.
ENTROPY_THRESHOLD = 4.0

# Where to LOOK for entropy candidates. We tokenise on whitespace +
# quote chars rather than scanning every substring — a secret in code
# is almost always a quoted literal or assignment value. This also
# keeps us from generating O(N²) substring noise on long files.
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")


def _shannon_entropy(s: str) -> float:
    """Standard Shannon entropy in bits/char. Empty string → 0.0."""
    if not s:
        return 0.0
    counts = Counter(s)
    total = len(s)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


# ── Regex catalog ────────────────────────────────────────────────────────


# Each pattern is anchored loosely so it can appear inside a string
# literal, dict value, or comment without requiring specific quoting.
# The (?:...) groups are non-capturing so the .group() default returns
# the full match for redacted previews.
#
# Severity defaults to "critical" unless the pattern has a meaningful
# false-positive risk (e.g. generic 32-hex which is also a common
# md5 or UUID-without-dashes).
#
# Stable rule_ids — they appear in audit logs, the security_specialist's
# system prompt, the gate's BLOCK reason, AND the AET-22 smoke test.
# Renames here MUST update those callsites (L23 cross-layer label drift).
_REGEX_CATALOG: list[tuple[str, re.Pattern[str], str]] = [
    # AWS access key — IAM convention is exactly 20 chars starting AKIA/ASIA
    (
        "aws_access_key_id",
        re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"),
        "critical",
    ),
    # AWS secret access key — 40-char base64 alphabet, very specific
    # surrounding shape ("aws_secret" or "secret_access_key" within 60
    # chars). Pure 40-char base64 elsewhere falls through to entropy.
    (
        "aws_secret_access_key",
        re.compile(
            r"(?i)aws.{0,20}secret.{0,20}[=:\s\"']{1,3}([A-Za-z0-9/+=]{40})\b",
        ),
        "critical",
    ),
    # Anthropic AWS workspace ID — internal format we use in this repo.
    # Add to the catalog because (a) it's a real credential identifier
    # in the deployment env and (b) L20 said the policy_check entry
    # catches it post-write; we want it caught PRE-write too.
    (
        "anthropic_aws_workspace_id",
        re.compile(r"\bwks_[A-Za-z0-9_-]{16,64}\b"),
        "critical",
    ),
    # Anthropic API key — sk-ant-… legacy direct-API key format.
    (
        "anthropic_api_key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        "critical",
    ),
    # GitHub personal access token (classic + fine-grained).
    (
        "github_pat",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"),
        "critical",
    ),
    # GitHub fine-grained PAT (newer, longer).
    (
        "github_fine_grained_pat",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"),
        "critical",
    ),
    # Slack tokens.
    (
        "slack_token",
        re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,48}\b"),
        "critical",
    ),
    # Generic JWT — 3 base64url segments separated by dots. Could be
    # a legitimate test fixture, but inside an emission it's almost
    # always a real token. Severity high (not critical) acknowledging
    # the test-fixture edge case.
    (
        "jwt_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "high",
    ),
    # PEM private-key block markers — anything inside these is bad.
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----",
        ),
        "critical",
    ),
    # PyPI / npm token formats.
    (
        "pypi_token",
        re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}\b"),
        "critical",
    ),
    (
        "npm_token",
        re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
        "critical",
    ),
    # Google API key — fixed AIza prefix + 35-char body.
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "critical",
    ),
    # Stripe live secret key.
    (
        "stripe_live_key",
        re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b"),
        "critical",
    ),
    # Generic 32+ hex string — captures md5, sha hashes, opaque tokens.
    # Severity high (not critical) because some of these are legitimately
    # hashes / commit shas in tests and docs.
    (
        "generic_32_hex_token",
        re.compile(r"\b[a-f0-9]{32,}\b"),
        "high",
    ),
]


# ── Allow-list ───────────────────────────────────────────────────────────


# Substrings that turn off ALL detection for a single line. Mirrors
# the convention used by detect-secrets and gitleaks. Comment-driven
# so tests/fixtures can opt out without polluting the rule catalog.
_ALLOW_MARKERS = ("pragma: allowlist secret", "secret-scan: ignore")

# Files we never scan — entropy detection on git/lock files would
# spam the agent with hashes that are intentionally there.
_SKIP_FILE_PATTERNS = re.compile(
    r"(?:^|/)(?:package-lock\.json|yarn\.lock|poetry\.lock|"
    r"Pipfile\.lock|\.git/|node_modules/|dist/|build/)",
)


def _redact(s: str) -> str:
    """Return first 4 + last 4 chars with `…` between them. For
    short matches (≤8 chars) return a fully-masked placeholder so the
    actual value never reaches the agent's prompt context."""
    if not s:
        return "<empty>"
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}…{s[-4:]}"


# ── Tool ─────────────────────────────────────────────────────────────────


class SecretScanTool:
    """In-memory secret scanner — runs on agent emissions before they
    hit disk."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "secret_scan",
            "description": (
                "Scan emitted file content (in-memory, BEFORE materialisation) "
                "for hard-coded credentials. Two layers: a regex catalog for "
                "known credential shapes (AWS keys, GitHub PATs, Anthropic "
                "workspace IDs, PEM headers, etc.) and a Shannon-entropy "
                "filter for high-randomness strings of ≥20 chars. Returns "
                "verdict='BLOCK' if any finding, 'PASS' otherwise. Matched "
                "strings are redacted in the response (first 4 + last 4 chars)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "emissions": {
                        "type": "array",
                        "description": (
                            "List of {file_path, content} dicts. "
                            "file_path is informational (used in findings); "
                            "content is the literal emitted text to scan."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["file_path", "content"],
                        },
                    },
                },
                "required": ["emissions"],
            },
        }

    def _scan_line(
        self, file_path: str, lineno: int, line: str,
    ) -> list[dict[str, Any]]:
        """Run the catalog + entropy pass on a single line. Returns
        zero or more finding dicts."""
        if any(marker in line for marker in _ALLOW_MARKERS):
            return []

        out: list[dict[str, Any]] = []

        # Layer 1: regex catalog. Track which substrings already matched
        # so the entropy pass doesn't double-flag the same span.
        already_matched_spans: list[tuple[int, int]] = []
        for rule_id, pattern, severity in _REGEX_CATALOG:
            for m in pattern.finditer(line):
                # Prefer group(1) if present (used for AWS secret-key
                # capture where the full match includes context); fall
                # back to the whole match.
                try:
                    matched = m.group(1)
                    span = m.span(1)
                except IndexError:
                    matched = m.group(0)
                    span = m.span()
                already_matched_spans.append(span)
                out.append({
                    "rule_id": rule_id,
                    "severity": severity,
                    "file": file_path,
                    "line": lineno,
                    "match_preview": _redact(matched),
                    "detector": "regex",
                })

        # Layer 2: entropy. Only consider tokens not already caught by
        # the catalog so a single secret doesn't produce two findings.
        for m in _TOKEN_RE.finditer(line):
            span = m.span()
            if any(
                span[0] >= ms and span[1] <= me
                for ms, me in already_matched_spans
            ):
                continue
            token = m.group(0)
            if len(token) < ENTROPY_MIN_LEN:
                continue
            entropy = _shannon_entropy(token)
            if entropy < ENTROPY_THRESHOLD:
                continue
            out.append({
                "rule_id": "shannon_entropy",
                "severity": "high",
                "file": file_path,
                "line": lineno,
                "match_preview": _redact(token),
                "detector": "entropy",
                "entropy_bits_per_char": round(entropy, 2),
            })

        return out

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        emissions: list[dict[str, Any]] = params.get("emissions") or []
        findings: list[dict[str, Any]] = []
        files_scanned = 0
        files_skipped = 0

        for em in emissions:
            file_path = (em.get("file_path") or "").strip()
            content = em.get("content") or ""
            if not file_path or not content:
                continue
            if _SKIP_FILE_PATTERNS.search(file_path):
                files_skipped += 1
                continue
            files_scanned += 1
            for lineno, line in enumerate(content.splitlines(), start=1):
                findings.extend(self._scan_line(file_path, lineno, line))

        verdict = "BLOCK" if findings else "PASS"

        if verdict == "PASS":
            summary = (
                f"Verdict: PASS — {files_scanned} file(s) scanned, "
                f"{files_skipped} skipped, no secrets detected."
            )
        else:
            critical = sum(1 for f in findings if f["severity"] == "critical")
            high = sum(1 for f in findings if f["severity"] == "high")
            first = findings[0]
            summary = (
                f"Verdict: BLOCK — {len(findings)} secret finding(s) "
                f"({critical} critical, {high} high); first: "
                f"{first['rule_id']} at {first['file']}:{first['line']}"
            )

        logger.info(
            "secret_scan_complete",
            verdict=verdict,
            files_scanned=files_scanned,
            files_skipped=files_skipped,
            finding_count=len(findings),
        )

        return {
            "verdict": verdict,
            "findings": findings,
            "summary": summary,
        }
