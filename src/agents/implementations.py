"""Concrete agent implementations — one class per agent role."""

import json
import re
from typing import Any

from src.agents.base import BaseAgent


class EngineeringLeadAgent(BaseAgent):
    """Decomposes requests and delegates to team leads."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        # Try to parse JSON delegation plan
        try:
            # Find JSON block in output
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group())
        except json.JSONDecodeError:
            pass
        return {"delegation_plan": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []  # Engineering Lead produces plans, not file artifacts


class PRDSpecialistAgent(BaseAgent):
    """Creates Product Requirements Documents."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"prd_document": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        # Extract any file paths mentioned
        paths = re.findall(r'(?:docs|reports)/[\w/.-]+\.md', text)
        return paths


class UserStoryAuthorAgent(BaseAgent):
    """Creates user stories with acceptance criteria."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"user_stories": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        paths = re.findall(r'(?:docs|stories)/[\w/.-]+\.md', text)
        return paths


class CodeReviewerAgent(BaseAgent):
    """Reviews code, creates branches, coordinates development."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"review_report": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []


class BackendSpecialistAgent(BaseAgent):
    """Implements backend code and tests."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"backend_code": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        paths = re.findall(r'(?:src|tests)/[\w/.-]+\.py', text)
        return paths


class FrontendSpecialistAgent(BaseAgent):
    """Implements frontend code and tests."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"frontend_code": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        paths = re.findall(r'(?:frontend/src|frontend/tests)/[\w/.-]+\.(?:tsx?|css)', text)
        return paths


class DevOpsSpecialistAgent(BaseAgent):
    """Manages deployments, CI/CD, infrastructure."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"deployment_report": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        paths = re.findall(r'(?:\.github/workflows|config)/[\w/.-]+\.ya?ml', text)
        return paths


class TesterSpecialistAgent(BaseAgent):
    """Writes and runs tests, reports coverage."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"test_report": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        paths = re.findall(r'(?:tests|e2e)/[\w/.-]+\.(?:py|ts)', text)
        return paths


class ResearchSpecialistAgent(BaseAgent):
    """Conducts research and produces assessment reports."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"research_report": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []


class ContentCreatorAgent(BaseAgent):
    """Creates presentations, documents, and guides."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"content_artifact": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        return []
