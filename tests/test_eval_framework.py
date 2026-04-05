"""P8-T16: Evaluation test framework — quality rubrics for agent outputs."""

import re
import pytest
from dataclasses import dataclass


@dataclass
class EvalResult:
    score: float
    checks: dict[str, bool]
    threshold: float
    passed: bool = False

    def __post_init__(self):
        self.passed = self.score >= self.threshold


class PRDEvaluator:
    """Scores PRD document quality."""

    def evaluate(self, output: str, threshold: float = 0.8) -> EvalResult:
        checks = {
            "has_requirements_table": bool(re.search(r"\|.*REQ-\d+", output)),
            "has_acceptance_criteria": "acceptance criteria" in output.lower(),
            "has_priority_levels": any(p in output for p in ["Critical", "High", "Medium", "Low"]),
            "has_sections": all(s in output for s in ["Summary", "Requirements"]),
            "word_count_reasonable": 50 < len(output.split()) < 5000,
            "no_code_in_prd": "import " not in output.split("\n")[0] if output else True,
        }
        score = sum(checks.values()) / len(checks)
        return EvalResult(score=score, checks=checks, threshold=threshold)


class CodeEvaluator:
    """Scores generated code quality."""

    def evaluate(self, code: str, language: str = "python", threshold: float = 0.75) -> EvalResult:
        checks = {
            "not_empty": len(code.strip()) > 0,
            "reasonable_length": 3 < code.count("\n") < 500,
            "no_syntax_markers": "```" not in code,
            "no_dangerous_patterns": not any(
                p in code for p in ["eval(", "exec(", "os.system("]
            ),
        }
        if language == "python":
            checks["has_functions_or_classes"] = bool(
                re.search(r"(def |class )", code)
            )
            checks["has_type_hints"] = bool(
                re.search(r"(-> |: (str|int|float|bool|list|dict|None))", code)
            )
        elif language in ("typescript", "tsx"):
            checks["has_functions_or_components"] = bool(
                re.search(r"(function |const .* = |export )", code)
            )

        score = sum(checks.values()) / len(checks)
        return EvalResult(score=score, checks=checks, threshold=threshold)


class UserStoryEvaluator:
    """Scores user story quality."""

    def evaluate(self, output: str, threshold: float = 0.8) -> EvalResult:
        checks = {
            "has_as_a_format": bool(re.search(r"[Aa]s a", output)),
            "has_acceptance_criteria": bool(
                re.search(r"(acceptance criteria|given.*when.*then)", output, re.IGNORECASE)
            ),
            "has_priority": any(p in output for p in ["Critical", "High", "Medium", "Low"]),
            "has_story_id": bool(re.search(r"US-\d+", output)),
            "readable_length": 20 < len(output.split()) < 2000,
        }
        score = sum(checks.values()) / len(checks)
        return EvalResult(score=score, checks=checks, threshold=threshold)


# ── Tests ────────────────────────────────────────

def test_prd_evaluator_good_output():
    prd = """## Summary
    This is a product requirements document.

    ## Requirements
    | ID | Description | Priority |
    |---|---|---|
    | REQ-001 | Login page | High |
    | REQ-002 | Dashboard | Medium |

    ## Acceptance Criteria
    - User can log in with valid credentials
    """
    result = PRDEvaluator().evaluate(prd)
    assert result.passed is True
    assert result.score >= 0.8


def test_prd_evaluator_bad_output():
    result = PRDEvaluator().evaluate("Just some random text")
    assert result.passed is False
    assert result.score < 0.8


def test_code_evaluator_good_python():
    code = """
def calculate_total(items: list[dict]) -> float:
    total = 0.0
    for item in items:
        total += item.get("price", 0.0) * item.get("quantity", 1)
    return total


class OrderService:
    def __init__(self, db: str) -> None:
        self.db = db

    def process(self, order_id: str) -> bool:
        return True
"""
    result = CodeEvaluator().evaluate(code, language="python")
    assert result.passed is True


def test_code_evaluator_dangerous_code():
    code = "result = eval(user_input)"
    result = CodeEvaluator().evaluate(code, language="python")
    assert result.checks["no_dangerous_patterns"] is False


def test_code_evaluator_empty():
    result = CodeEvaluator().evaluate("", language="python")
    assert result.passed is False


def test_user_story_evaluator_good():
    story = """### US-001 Login Form

As a user, I want to log in with my credentials, so that I can access the dashboard.

Priority: High

Acceptance Criteria:
- Given valid credentials, when I submit, then I see the dashboard
- Given invalid password, when I submit, then I see an error
"""
    result = UserStoryEvaluator().evaluate(story)
    assert result.passed is True


def test_user_story_evaluator_bad():
    result = UserStoryEvaluator().evaluate("Make the thing work")
    assert result.passed is False


def test_eval_result_threshold():
    result = EvalResult(score=0.75, checks={"a": True}, threshold=0.8)
    assert result.passed is False
    result2 = EvalResult(score=0.85, checks={"a": True}, threshold=0.8)
    assert result2.passed is True
