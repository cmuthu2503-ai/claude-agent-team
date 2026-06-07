"""PII scanner (KB-05).

Phase-1 implementation is **regex-based** — emails, US SSNs, credit-card-like
numbers (Luhn-validated), phone numbers, and common secret/API-key shapes.
It flags a document's sensitivity so the curator + retention layers (§15)
treat it appropriately; it does NOT block ingest.

Deliberately lightweight: Presidio (spaCy + a language model) is a heavy
install for the platform's low-sensitivity docs. A ``PresidioPiiScanner``
can implement the same ``scan`` surface later behind config, with no caller
changes — the same swap pattern as the embedder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Common secret shapes: long base64-ish tokens, AWS keys, bearer prefixes.
_SECRET = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}"            # AWS access key id
    r"|sk-[A-Za-z0-9]{16,}"             # OpenAI-style
    r"|ghp_[A-Za-z0-9]{20,}"            # GitHub PAT
    r"|xox[baprs]-[A-Za-z0-9-]{10,})\b" # Slack
)


@dataclass
class PiiResult:
    has_pii: bool
    findings: list[str] = field(default_factory=list)  # finding TYPES, not values


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    checksum = 0
    parity = len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


class PiiScanner:
    """Regex PII detector. ``scan`` returns the finding TYPES found (never the
    raw values, so the audit/log surface doesn't itself leak PII)."""

    def scan(self, text: str) -> PiiResult:
        if not text:
            return PiiResult(has_pii=False)
        findings: list[str] = []
        if _EMAIL.search(text):
            findings.append("email")
        if _SSN.search(text):
            findings.append("ssn")
        if _PHONE.search(text):
            findings.append("phone")
        if _SECRET.search(text):
            findings.append("secret")
        # Card: regex candidates, then Luhn to cut false positives.
        for m in _CARD.finditer(text):
            if _luhn_ok(m.group(0)):
                findings.append("credit_card")
                break
        return PiiResult(has_pii=bool(findings), findings=findings)
