"""Concrete agent implementations — one class per agent role."""

import json
import re
from typing import Any

from src.agents.base import BaseAgent



class BusinessAnalystAgent(BaseAgent):
    """Business Analyst — owns the planning chain end to end: authors the PRD,
    then decomposes the finalized PRD into the epic→feature→task plan (user
    stories). Merges the former PRDSpecialist + UserStoryAuthor roles, which were
    sequential, same-domain, same-tier planning work."""

    def _parse_output(self, text: str) -> dict[str, Any]:
        return {"text": text}

    def _extract_artifacts(self, text: str) -> list[str]:
        # Union of the two former roles' artifact locations (PRD/report markdown
        # + user-story/spec markdown).
        return re.findall(r'(?:docs|reports|stories)/[\w/.-]+\.md', text)


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
