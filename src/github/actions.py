"""GitHub Actions setup — generates workflow files from templates."""

from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

TEMPLATE_DIR = Path("config/github-actions-templates")


class ActionsManager:
    """Generates .github/workflows/ from reference templates."""

    def __init__(self, template_dir: Path = TEMPLATE_DIR) -> None:
        self.template_dir = template_dir

    def generate_workflows(self, target_dir: Path, tech_stack: str = "python-react") -> list[str]:
        """Copy and adapt workflow templates to target project directory."""
        workflows_dir = target_dir / ".github" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)

        generated = []
        for template_file in self.template_dir.glob("*.yml"):
            content = template_file.read_text()

            # Adapt based on tech stack
            if tech_stack == "python-only":
                # Remove frontend jobs
                content = self._remove_frontend_jobs(content)

            output_path = workflows_dir / template_file.name
            output_path.write_text(content)
            generated.append(str(output_path))
            logger.info("workflow_generated", file=template_file.name)

        return generated

    def _remove_frontend_jobs(self, content: str) -> str:
        """Remove frontend-specific jobs from workflow content."""
        lines = content.split("\n")
        filtered = []
        skip = False
        for line in lines:
            if "lint-frontend:" in line or "format-frontend:" in line or "test-frontend:" in line or "npm-audit:" in line:
                skip = True
                continue
            if skip and line and not line.startswith(" "):
                skip = False
            if not skip:
                filtered.append(line)
        return "\n".join(filtered)

    def list_templates(self) -> list[str]:
        return [f.name for f in self.template_dir.glob("*.yml")]
