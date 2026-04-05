"""Input sanitizer — validates user input before it reaches any agent."""

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

MAX_INPUT_LENGTH = 10_000

BLOCKED_PATTERNS = [
    r"ignore\s+(previous|above|all)\s+instructions",
    r"you\s+are\s+now\s+a",
    r"system\s*:\s*",
    r"<\|.*?\|>",
    r"\[INST\]|\[/INST\]",
    r"<\|im_start\|>|<\|im_end\|>",
    r"Human:\s*\n\s*Assistant:",
    r"###\s*(System|Human|Assistant)\s*:",
]


@dataclass
class SanitizeResult:
    ok: bool
    cleaned: str = ""
    reason: str = ""


class InputSanitizer:
    """Validates and cleans user input before it reaches any agent.

    Three checks:
    1. Length limit
    2. Blocked prompt injection patterns
    3. Control token detection
    """

    def __init__(self, max_length: int = MAX_INPUT_LENGTH) -> None:
        self.max_length = max_length
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS
        ]

    def sanitize(self, user_input: str) -> SanitizeResult:
        """Validate and clean user input. Returns SanitizeResult."""
        # Check length
        if len(user_input) > self.max_length:
            logger.warning("input_too_long", length=len(user_input), max=self.max_length)
            return SanitizeResult(
                ok=False,
                reason=f"Input exceeds maximum length ({len(user_input)} > {self.max_length})",
            )

        # Check for empty input
        stripped = user_input.strip()
        if not stripped:
            return SanitizeResult(ok=False, reason="Input is empty")

        # Check for blocked patterns
        for pattern in self._compiled_patterns:
            match = pattern.search(stripped)
            if match:
                logger.warning(
                    "prompt_injection_detected",
                    pattern=pattern.pattern,
                    match=match.group()[:50],
                )
                return SanitizeResult(
                    ok=False,
                    reason=f"Input contains blocked pattern: {match.group()[:50]}",
                )

        return SanitizeResult(ok=True, cleaned=stripped)
