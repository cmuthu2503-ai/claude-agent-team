"""Aggregator — collects subtask results and synthesizes unified output."""

from typing import Any

import structlog

logger = structlog.get_logger()


class AggregationError(Exception):
    """Raised when aggregation fails."""


class Aggregator:
    """Collects and combines results from multiple agent subtasks."""

    def aggregate(self, results: list[dict[str, Any]], allow_partial: bool = True) -> dict[str, Any]:
        successes = [r for r in results if r.get("status") != "failed"]
        failures = [r for r in results if r.get("status") == "failed"]

        if not successes and failures:
            raise AggregationError(
                f"All {len(failures)} subtasks failed. "
                f"Errors: {[f.get('error') for f in failures]}"
            )

        if failures and not allow_partial:
            raise AggregationError(
                f"{len(failures)} subtask(s) failed and partial results not allowed."
            )

        if failures:
            logger.warning(
                "partial_aggregation",
                total=len(results), succeeded=len(successes), failed=len(failures),
            )

        combined_artifacts: list[str] = []
        combined_outputs: dict[str, Any] = {}

        for result in successes:
            combined_artifacts.extend(result.get("artifacts", []))
            combined_outputs.update(result.get("outputs", {}))

        return {
            "status": "completed" if not failures else "partial",
            "artifacts": combined_artifacts,
            "outputs": combined_outputs,
            "succeeded": len(successes),
            "failed": len(failures),
            "errors": [f.get("error") for f in failures],
        }

    def build_summary(self, aggregated: dict[str, Any], request_description: str) -> str:
        status = aggregated["status"]
        artifact_count = len(aggregated.get("artifacts", []))
        summary_parts = [
            f"Request: {request_description}",
            f"Status: {status}",
            f"Artifacts produced: {artifact_count}",
        ]
        if aggregated.get("errors"):
            summary_parts.append(f"Errors: {', '.join(str(e) for e in aggregated['errors'])}")
        return "\n".join(summary_parts)
