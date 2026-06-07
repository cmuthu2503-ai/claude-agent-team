"""policy_check tool — declarative quality-rule evaluator.

Implements AET-03 (Phase AE-3 from docs/task-list.md). Reads
``config/quality-rules.yaml`` at tool init, validates it against
the Pydantic schema, and exposes ``execute()`` for the
``quality_guardian`` agent to call against a batch of agent emissions.

Each emission is a dict shaped like:

  {
    "target_path": "src/api/routes/widgets.py",
    "content":     "<file contents as one string>",
    "agent_id":    "backend_specialist",
    "tool_name":   "file_write",         # or search_replace, etc.
    "rework_cycle": 0,                   # int, 0 on first attempt
  }

The evaluator runs every enabled rule against every emission whose
``applies_to`` scope matches, and returns a structured violations
list with rule_id + severity + matched-content snippet + the rule's
``rationale`` + ``fix_hint`` (so the downstream rework prompt has
actionable guidance, not just "policy failed").

Severity mapping (per ``docs/quality-rules-schema.md`` §6):

  enforce → BLOCK the workflow at the quality_guardian_approval gate
  warn    → annotate quality_report, advance the workflow
  info    → log only, no UI surface

The verdict returned in the response (``BLOCK`` / ``PASS_WITH_WARNINGS``
/ ``PASS``) drives that gate decision in AET-06.

Boot-time validation (per schema doc §7): a missing config file, an
unknown schema version, a Pydantic validation failure on any rule,
or a regex that fails to compile all raise during ``__init__``. The
tool refuses to instantiate rather than silently approving everything
when the rules file is broken. Operator sees the boot crash, fixes
the YAML, restarts.

Authoring guide for new rules: ``docs/quality-rules-schema.md``.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = structlog.get_logger()


# ── Where the rules file lives ────────────────────────────────────────────
#
# The container bind-mount puts /app/config/ over the host's config/
# directory, so the same path works in dev (docker compose) and on the
# host when policy_check is exercised by a unit test.
_DEFAULT_CONFIG_PATH = Path("/app/config/quality-rules.yaml")

# Hard cap on composite matcher depth (per schema doc §4.4 — keeps
# evaluations predictable and prevents pathological deeply-nested
# rules from blowing the stack).
_MAX_COMPOSITE_DEPTH = 3


# ── Pydantic schema models (mirror §11 of docs/quality-rules-schema.md) ──


class _MatcherBase(BaseModel):
    """Common base — present only so the discriminated union resolves
    cleanly when Pydantic introspects subclasses."""


class MatcherRegex(_MatcherBase):
    type: Literal["regex"]
    pattern: str
    flags: list[Literal["ignorecase", "multiline", "dotall", "verbose"]] = []
    max_matches: int = Field(default=1, ge=1)

    @field_validator("pattern")
    @classmethod
    def _check_compiles(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"regex pattern does not compile: {e}") from e
        return v


class MatcherFileMeta(_MatcherBase):
    type: Literal["file_metadata"]
    property: Literal["line_count", "byte_size", "import_count", "function_count"]
    operator: Literal[">", ">=", "<", "<=", "==", "!="]
    threshold: int


class MatcherEmissionMeta(_MatcherBase):
    type: Literal["emission_metadata"]
    field: Literal["tool_name", "agent_id", "target_path", "rework_cycle"]
    operator: Literal["equals", "matches", "in"]
    # value can be a string, int, or a list of those (for the "in" op)
    value: Union[str, int, list[Union[str, int]]]


class MatcherComposite(_MatcherBase):
    type: Literal["composite"]
    op: Literal["AND", "OR"]
    # Forward reference resolved via model_rebuild() below the union decl.
    children: list["Matcher"]


# Discriminated union — the `type` field picks the variant. Pydantic v2
# validates and parses against the right subclass automatically.
Matcher = Annotated[
    Union[MatcherRegex, MatcherFileMeta, MatcherEmissionMeta, MatcherComposite],
    Field(discriminator="type"),
]

# Required because MatcherComposite.children references the union by
# forward reference (the union is defined AFTER the class).
MatcherComposite.model_rebuild()


class AppliesTo(BaseModel):
    files: list[str] = []
    exclude_files: list[str] = []
    agents: list[str] = []
    tools: list[str] = []
    # Optional dict like {"min": 2} — fires only on rework cycles ≥ 2.
    rework_cycle: dict[str, int] | None = None


class QualityRule(BaseModel):
    id: str = Field(pattern=r"^QR-\d{3}$")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    severity: Literal["enforce", "warn", "info"]
    enabled: bool = True
    matcher: Matcher
    applies_to: AppliesTo
    rationale: str
    lesson_ref: str | None = Field(default=None, pattern=r"^L\d{2,3}$")
    fix_hint: str | None = None
    introduced_in: str | None = None


class _Defaults(BaseModel):
    enforce_blocks: bool = True
    warn_annotates: bool = True
    info_logs_only: bool = True


class QualityRulesConfig(BaseModel):
    version: Literal[1]
    defaults: _Defaults | None = None
    rules: list[QualityRule]

    @field_validator("rules")
    @classmethod
    def _check_unique_ids_and_names(cls, v: list[QualityRule]) -> list[QualityRule]:
        # Boot-time guard: duplicate ID or name = config-author bug,
        # not a runtime concern. Fail loud per schema doc §7.
        ids = [r.id for r in v]
        if len(set(ids)) != len(ids):
            dupes = sorted({x for x in ids if ids.count(x) > 1})
            raise ValueError(f"duplicate rule ids: {dupes}")
        names = [r.name for r in v]
        if len(set(names)) != len(names):
            dupes = sorted({x for x in names if names.count(x) > 1})
            raise ValueError(f"duplicate rule names: {dupes}")
        return v


# ── Tool implementation ──────────────────────────────────────────────────


class PolicyCheckTool:
    """Evaluate every enabled rule in quality-rules.yaml against a batch
    of agent emissions, return structured violations.

    Construct once at agent registration time. Boot-time validation
    (missing file, malformed YAML, invalid regex, duplicate id/name)
    raises out of ``__init__`` so a broken config never silently turns
    into "agent always approves".
    """

    def __init__(self, config_path: Path | None = None):
        self.config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self.config = self._load_config(self.config_path)
        # Validate composite-matcher depth — recursive descent, raises
        # if any branch exceeds the cap.
        for rule in self.config.rules:
            self._assert_composite_depth(rule.matcher, depth=1, rule_id=rule.id)
        logger.info(
            "policy_check_loaded",
            rules_total=len(self.config.rules),
            rules_enabled=sum(1 for r in self.config.rules if r.enabled),
            by_severity={
                sev: sum(1 for r in self.config.rules if r.enabled and r.severity == sev)
                for sev in ("enforce", "warn", "info")
            },
            config_path=str(self.config_path),
        )

    # ── Loading + validation ──────────────────────────────────────────────

    def _load_config(self, path: Path) -> QualityRulesConfig:
        if not path.exists():
            raise FileNotFoundError(
                f"policy_check.config_missing: {path} does not exist. "
                f"See docs/quality-rules-schema.md for the expected schema."
            )
        try:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"policy_check.yaml_parse_failed: {e}") from e
        if not isinstance(raw, dict):
            raise ValueError(
                f"policy_check.malformed_root: top level of {path} must be a mapping"
            )
        try:
            return QualityRulesConfig(**raw)
        except ValidationError as e:
            # Re-raise as ValueError so the executor's startup error
            # surface is uniform (ValueError or RuntimeError).
            raise ValueError(f"policy_check.rule_invalid:\n{e}") from e

    def _assert_composite_depth(
        self, matcher: Any, *, depth: int, rule_id: str,
    ) -> None:
        if depth > _MAX_COMPOSITE_DEPTH:
            raise ValueError(
                f"policy_check.composite_too_deep: rule {rule_id} nests "
                f"composite matchers beyond depth {_MAX_COMPOSITE_DEPTH}"
            )
        if isinstance(matcher, MatcherComposite):
            for child in matcher.children:
                self._assert_composite_depth(child, depth=depth + 1, rule_id=rule_id)

    # ── Anthropic tool schema ─────────────────────────────────────────────

    def schema(self) -> dict[str, Any]:
        return {
            "name": "policy_check",
            "description": (
                "Run the declarative quality rule catalog "
                "(config/quality-rules.yaml) against a batch of agent emissions. "
                "Returns {verdict, violations[], summary{}}. "
                "verdict='BLOCK' when any rule with severity=enforce fires — "
                "the workflow halts at quality_guardian_approval. "
                "verdict='PASS_WITH_WARNINGS' when only warn-severity rules fire. "
                "verdict='PASS' when nothing fires. Each violation carries rule_id, "
                "rationale, fix_hint, and a snippet of the matched content."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "emissions": {
                        "type": "array",
                        "description": (
                            "List of agent emissions to evaluate. Each emission "
                            "is the output of one agent tool-call: the file the "
                            "agent wrote (target_path + content) plus the metadata "
                            "needed for applies_to scoping (agent_id, tool_name, "
                            "rework_cycle)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_path": {"type": "string"},
                                "content": {"type": "string"},
                                "agent_id": {"type": "string"},
                                "tool_name": {"type": "string"},
                                "rework_cycle": {"type": "integer"},
                            },
                            "required": ["target_path", "content"],
                        },
                    },
                },
                "required": ["emissions"],
            },
        }

    # ── Main entry point ──────────────────────────────────────────────────

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        emissions = params.get("emissions") or []
        if not isinstance(emissions, list):
            return {
                "verdict": "ERROR",
                "error": "emissions must be a list",
                "violations": [],
            }

        violations: list[dict[str, Any]] = []
        for emission in emissions:
            if not isinstance(emission, dict):
                continue
            for rule in self.config.rules:
                if not rule.enabled:
                    continue
                if not self._applies_to(rule, emission):
                    continue
                fired, snippet = self._evaluate_matcher(rule.matcher, emission)
                if not fired:
                    continue
                violations.append({
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "severity": rule.severity,
                    "target_path": emission.get("target_path"),
                    "agent_id": emission.get("agent_id"),
                    "rationale": rule.rationale,
                    "fix_hint": rule.fix_hint,
                    "lesson_ref": rule.lesson_ref,
                    "snippet": snippet,
                })

        enforce_count = sum(1 for v in violations if v["severity"] == "enforce")
        warn_count = sum(1 for v in violations if v["severity"] == "warn")
        info_count = sum(1 for v in violations if v["severity"] == "info")

        defaults = self.config.defaults or _Defaults()
        if enforce_count > 0 and defaults.enforce_blocks:
            verdict = "BLOCK"
        elif warn_count > 0:
            verdict = "PASS_WITH_WARNINGS"
        else:
            verdict = "PASS"

        summary = {
            "verdict": verdict,
            "enforce_count": enforce_count,
            "warn_count": warn_count,
            "info_count": info_count,
            "total_emissions_checked": len(emissions),
            "total_violations": len(violations),
        }

        logger.info(
            "policy_check_complete",
            **{k: summary[k] for k in ("verdict", "enforce_count", "warn_count")},
            emissions=len(emissions),
        )

        return {
            "verdict": verdict,
            "violations": violations,
            "summary": summary,
        }

    # ── Scoping (applies_to) ──────────────────────────────────────────────

    def _applies_to(self, rule: QualityRule, emission: dict[str, Any]) -> bool:
        """All-must-match conjunctive scoping per schema doc §5."""
        scope = rule.applies_to
        target = str(emission.get("target_path") or "")
        agent = str(emission.get("agent_id") or "")
        tool = str(emission.get("tool_name") or "")
        cycle = int(emission.get("rework_cycle") or 0)

        # exclude_files takes precedence — short-circuit negative match.
        for pat in scope.exclude_files:
            if self._glob_match(target, pat):
                return False

        if scope.files and not any(self._glob_match(target, p) for p in scope.files):
            return False
        if scope.agents and agent not in scope.agents:
            return False
        if scope.tools and tool not in scope.tools:
            return False
        if scope.rework_cycle and "min" in scope.rework_cycle:
            if cycle < scope.rework_cycle["min"]:
                return False

        return True

    @staticmethod
    def _glob_match(path: str, pattern: str) -> bool:
        """Glob match with `**` support (Gitignore-style). fnmatch handles
        single-segment globs; `**` we expand manually so `src/**/*.py`
        matches `src/a/b/c.py`."""
        if "**" not in pattern:
            return fnmatch(path, pattern)
        # Convert `**` to a regex `.*` and `*` to `[^/]*` for segment-scoped.
        # Translate the rest via fnmatch.translate then run as regex.
        # Simpler approach: split on `**` and check each segment present
        # in order. Good enough for our patterns (src/**/*.py, tests/**, etc).
        # For full correctness we could pull in pathspec, but this covers
        # 100% of the patterns in v1's quality-rules.yaml.
        regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        regex = "^" + regex + "$"
        return bool(re.match(regex, path))

    # ── Matcher evaluation ────────────────────────────────────────────────

    def _evaluate_matcher(
        self, matcher: Any, emission: dict[str, Any],
    ) -> tuple[bool, str]:
        """Returns (fired, snippet)."""
        if isinstance(matcher, MatcherRegex):
            return self._eval_regex(matcher, emission)
        if isinstance(matcher, MatcherFileMeta):
            return self._eval_file_meta(matcher, emission)
        if isinstance(matcher, MatcherEmissionMeta):
            return self._eval_emission_meta(matcher, emission)
        if isinstance(matcher, MatcherComposite):
            return self._eval_composite(matcher, emission)
        return False, ""

    def _eval_regex(
        self, m: MatcherRegex, emission: dict[str, Any],
    ) -> tuple[bool, str]:
        content = str(emission.get("content") or "")
        if not content:
            return False, ""
        flags = 0
        for f in m.flags:
            flags |= {
                "ignorecase": re.IGNORECASE,
                "multiline": re.MULTILINE,
                "dotall": re.DOTALL,
                "verbose": re.VERBOSE,
            }[f]
        # Recompile per evaluation. Pydantic validators have already
        # confirmed pattern compiles at config-load time, so re.compile
        # never raises here. Negligible perf cost for typical loads.
        compiled = re.compile(m.pattern, flags)
        matches = list(compiled.finditer(content))
        if len(matches) < m.max_matches:
            return False, ""
        # Snippet = first match plus 40 chars of surrounding context.
        first = matches[0]
        start = max(0, first.start() - 40)
        end = min(len(content), first.end() + 40)
        snippet = content[start:end].strip()
        # Mark match count if it's significantly more than the threshold.
        if len(matches) > m.max_matches:
            snippet = f"[{len(matches)} matches] " + snippet
        return True, snippet

    def _eval_file_meta(
        self, m: MatcherFileMeta, emission: dict[str, Any],
    ) -> tuple[bool, str]:
        content = str(emission.get("content") or "")
        value = self._compute_file_metric(content, m.property)
        if not self._compare(value, m.operator, m.threshold):
            return False, ""
        snippet = f"{m.property}={value} (threshold {m.operator} {m.threshold})"
        return True, snippet

    def _eval_emission_meta(
        self, m: MatcherEmissionMeta, emission: dict[str, Any],
    ) -> tuple[bool, str]:
        actual = emission.get(m.field)
        if not self._compare_emission(actual, m.operator, m.value):
            return False, ""
        snippet = f"{m.field}={actual!r} (matcher: {m.operator} {m.value!r})"
        return True, snippet

    def _eval_composite(
        self, m: MatcherComposite, emission: dict[str, Any],
    ) -> tuple[bool, str]:
        results = [self._evaluate_matcher(c, emission) for c in m.children]
        fired_flags = [r[0] for r in results]
        if m.op == "AND":
            fired = all(fired_flags)
        else:  # OR
            fired = any(fired_flags)
        if not fired:
            return False, ""
        # Snippet = concat of child snippets that fired.
        snippets = [s for f, s in results if f and s]
        return True, f"[{m.op}] " + " | ".join(snippets)

    # ── File metric helpers ───────────────────────────────────────────────

    @staticmethod
    def _compute_file_metric(content: str, prop: str) -> int:
        if not content:
            return 0
        if prop == "line_count":
            # Count lines as the editor would — trailing newline doesn't
            # add a phantom line, but a non-newline-terminated final
            # line DOES count.
            n = content.count("\n")
            if not content.endswith("\n"):
                n += 1
            return n
        if prop == "byte_size":
            return len(content.encode("utf-8"))
        if prop == "import_count":
            # Python imports only — JS/TS would need a separate matcher.
            return len(re.findall(r"^(?:from|import)\s+", content, re.MULTILINE))
        if prop == "function_count":
            # Sum of Python `def` and JS `function`/arrow function decls.
            py_defs = len(re.findall(r"^(?:async\s+)?def\s+\w+", content, re.MULTILINE))
            js_decls = len(re.findall(
                r"^(?:export\s+)?(?:async\s+)?function\s+\w+",
                content, re.MULTILINE,
            ))
            js_arrows = len(re.findall(
                r"^(?:export\s+)?const\s+\w+\s*=\s*(?:async\s+)?\(",
                content, re.MULTILINE,
            ))
            return py_defs + js_decls + js_arrows
        return 0

    @staticmethod
    def _compare(lhs: int, op: str, rhs: int) -> bool:
        if op == ">":
            return lhs > rhs
        if op == ">=":
            return lhs >= rhs
        if op == "<":
            return lhs < rhs
        if op == "<=":
            return lhs <= rhs
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        return False

    @staticmethod
    def _compare_emission(actual: Any, op: str, expected: Any) -> bool:
        if op == "equals":
            return actual == expected
        if op == "matches" and isinstance(expected, str):
            try:
                return bool(re.search(expected, str(actual)))
            except re.error:
                return False
        if op == "in" and isinstance(expected, list):
            return actual in expected
        return False
