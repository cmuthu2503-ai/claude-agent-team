"""Weekly report generator — creates summary reports from state data."""

from datetime import datetime, timedelta
from pathlib import Path

import structlog

from src.state.base import StateStore

logger = structlog.get_logger()


class WeeklyReportGenerator:
    """Generates weekly summary reports from state store data."""

    def __init__(self, state: StateStore) -> None:
        self.state = state

    async def generate(self) -> str:
        """Generate the weekly report as markdown."""
        now = datetime.utcnow()
        week_start = now - timedelta(days=7)
        date_range = f"{week_start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}"

        # Gather data
        all_requests = await self.state.list_requests(limit=100)
        weekly_requests = [r for r in all_requests if r.created_at >= week_start]

        completed = [r for r in weekly_requests if r.status == "completed"]
        failed = [r for r in weekly_requests if r.status == "failed"]
        in_progress = [r for r in weekly_requests if r.status == "in_progress"]

        daily_cost = await self.state.get_daily_cost()
        monthly_cost = await self.state.get_monthly_cost()

        deployments = await self.state.list_deployments(limit=20)
        weekly_deploys = [d for d in deployments if d.deployed_at and d.deployed_at >= week_start]

        # Build report
        report = f"""## Weekly Task Report — {date_range}

### Summary
| Category | Total | Done | In Progress | Failed |
|----------|-------|------|-------------|--------|
| Requests | {len(weekly_requests)} | {len(completed)} | {len(in_progress)} | {len(failed)} |

### Cost
- Today: ${daily_cost:.2f}
- This Month: ${monthly_cost:.2f}

### Deployments
- Deployments this week: {len(weekly_deploys)}

### Highlights
"""
        if completed:
            for r in completed[:5]:
                report += f"- {r.request_id}: {r.description[:60]}\n"
        else:
            report += "- No completed requests this week\n"

        if failed:
            report += "\n### Blockers\n"
            for r in failed:
                report += f"- {r.request_id}: {r.description[:60]} (FAILED)\n"

        report += f"\n---\n*Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}*\n"
        return report

    async def save(self, report: str, output_dir: str = "reports/weekly") -> str:
        """Save the report to a markdown file."""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        filename = f"{datetime.utcnow().strftime('%Y-%m-%d')}.md"
        filepath = Path(output_dir) / filename
        filepath.write_text(report)
        logger.info("weekly_report_saved", path=str(filepath))
        return str(filepath)
