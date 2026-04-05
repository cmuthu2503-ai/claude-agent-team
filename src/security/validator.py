"""Output validator — checks agent outputs for dangerous patterns."""

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

DANGEROUS_CODE_PATTERNS = [
    (r"subprocess\.call\(.*shell\s*=\s*True", "subprocess with shell=True"),
    (r"subprocess\.Popen\(.*shell\s*=\s*True", "subprocess.Popen with shell=True"),
    (r"os\.system\(", "os.system call"),
    (r"(?<!\w)eval\(", "eval() call"),
    (r"(?<!\w)exec\(", "exec() call"),
    (r"__import__\(", "dynamic import"),
    (r"rm\s+-rf\s+/(?!\w)", "rm -rf / command"),
    (r"chmod\s+777", "chmod 777"),
    (r"curl\s+.*\|\s*(?:bash|sh)", "curl pipe to shell"),
    (r"wget\s+.*\|\s*(?:bash|sh)", "wget pipe to shell"),
]

DANGEROUS_FILE_PATTERNS = [
    (r"\.env(?:$|\.)", "environment file modification"),
    (r"/etc/passwd", "system file access"),
    (r"/etc/shadow", "system file access"),
    (r"~/.ssh/", "SSH key access"),
]


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] | None = None


class OutputValidator:
    """Validates agent outputs before they become artifacts or actions."""

    def __init__(self) -> None:
        self._code_patterns = [
            (re.compile(p, re.IGNORECASE), desc) for p, desc in DANGEROUS_CODE_PATTERNS
        ]
        self._file_patterns = [
            (re.compile(p), desc) for p, desc in DANGEROUS_FILE_PATTERNS
        ]

    def validate_code(self, code: str) -> ValidationResult:
        """Check generated code for dangerous patterns."""
        issues = []
        for pattern, description in self._code_patterns:
            if pattern.search(code):
                issues.append(f"Dangerous pattern: {description}")
                logger.warning("dangerous_code_pattern", pattern=description)

        if issues:
            return ValidationResult(ok=False, issues=issues)
        return ValidationResult(ok=True)

    def validate_file_path(self, file_path: str) -> ValidationResult:
        """Check if a file path targets sensitive locations."""
        issues = []
        for pattern, description in self._file_patterns:
            if pattern.search(file_path):
                issues.append(f"Sensitive file: {description}")
                logger.warning("sensitive_file_access", path=file_path, reason=description)

        # Check for path traversal
        if ".." in file_path:
            issues.append("Path traversal detected")

        if issues:
            return ValidationResult(ok=False, issues=issues)
        return ValidationResult(ok=True)

    def validate_output(self, output: str) -> ValidationResult:
        """Validate any agent text output for dangerous patterns."""
        code_result = self.validate_code(output)
        if not code_result.ok:
            return code_result
        return ValidationResult(ok=True)
