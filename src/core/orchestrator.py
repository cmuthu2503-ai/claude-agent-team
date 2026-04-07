"""Orchestrator — main entry point. Manages request lifecycle end-to-end."""

import asyncio
import random
import uuid
from datetime import datetime
from typing import Any

import structlog

from src.config.loader import ConfigLoader
from src.core.aggregator import Aggregator
from src.core.dispatcher import Dispatcher
from src.core.events import EventEmitter
import re

from src.models.base import AcceptanceCriterion, Document, Request, RequestStatus, Story, StoryStatus, Subtask, SubtaskStatus, TestCase
from src.state.base import StateStore
from src.workflows.loader import WorkflowLoader
from src.workflows.runner import AgentExecutor, WorkflowRunner

logger = structlog.get_logger()

# Simulated processing time per agent (seconds) when using mock executor
MOCK_AGENT_DELAYS: dict[str, tuple[float, float]] = {
    "engineering_lead": (1.5, 3.0),
    "prd_specialist": (3.0, 6.0),
    "user_story_author": (2.0, 4.0),
    "code_reviewer": (2.0, 5.0),
    "backend_specialist": (4.0, 8.0),
    "frontend_specialist": (4.0, 8.0),
    "devops_specialist": (2.0, 4.0),
    "tester_specialist": (3.0, 6.0),
}


class Orchestrator(AgentExecutor):
    """Top-level coordinator. Submits requests, runs workflows, aggregates results."""

    def __init__(
        self,
        config: ConfigLoader,
        state: StateStore,
        events: EventEmitter,
    ) -> None:
        self.config = config
        self.state = state
        self.events = events
        self.dispatcher = Dispatcher(config)
        self.aggregator = Aggregator()
        self.workflow_loader = WorkflowLoader(config)
        self.workflow_loader.load_all()
        self.runner = WorkflowRunner(
            executor=self,
            code_commit_handler=self._handle_code_commit,
            publish_handler=self._handle_publish,
        )
        self._agent_executor: Any = None  # Set by agent system in Phase 3
        self._background_tasks: set[asyncio.Task] = set()

        # Token tracker for cost recording
        from src.core.token_tracker import TokenTracker
        self._token_tracker = TokenTracker(state)

        # Code writer for Level 3 deployment
        from src.core.code_writer import CodeWriter
        self._code_writer = CodeWriter(state)

        # Research publisher for the research → docs/ + GitHub flow
        from src.core.research_publisher import ResearchPublisher
        self._research_publisher = ResearchPublisher()

    def set_agent_executor(self, executor: Any) -> None:
        """Inject the real agent executor (set during Phase 3 agent system init)."""
        self._agent_executor = executor

    async def submit(self, description: str, task_type: str = "feature_request",
                     priority: str = "medium", created_by: str = "",
                     provider: str = "anthropic") -> Request:
        request_id = f"REQ-{uuid.uuid4().hex[:6].upper()}"
        request = Request(
            request_id=request_id,
            description=description,
            task_type=task_type,
            priority=priority,
            created_by=created_by,
            provider=provider,
        )
        await self.state.create_request(request)
        await self.events.emit("request.created", {
            "request_id": request_id, "description": description, "task_type": task_type,
        })
        logger.info("request_submitted", request_id=request_id, task_type=task_type)

        # Check for existing documents (duplicate detection)
        skip_stages: list[str] = []
        reused_artifacts: dict[str, Any] = {}
        existing_prds = await self.state.search_documents(description, doc_type="prd", limit=3)
        existing_stories = await self.state.search_documents(description, doc_type="user_stories", limit=3)

        if existing_prds:
            best_prd = existing_prds[0]
            keywords = self._extract_keywords(description)
            prd_keywords = self._extract_keywords(best_prd.title + " " + best_prd.content[:500])
            overlap = len(set(keywords) & set(prd_keywords))
            confidence = overlap / max(len(keywords), 1)

            if confidence >= 0.5:  # 50%+ keyword overlap
                reused_artifacts["prd_document"] = best_prd.content
                reused_artifacts["reused_prd_id"] = best_prd.document_id
                reused_artifacts["reused_prd_request"] = best_prd.request_id
                skip_stages.append("requirements")
                logger.info("reusing_prd", document_id=best_prd.document_id,
                           from_request=best_prd.request_id, confidence=f"{confidence:.0%}")
                await self.events.emit("document.reused", {
                    "request_id": request_id, "doc_type": "prd",
                    "from_request": best_prd.request_id, "confidence": f"{confidence:.0%}",
                })

                if existing_stories:
                    best_stories = existing_stories[0]
                    reused_artifacts["user_stories"] = best_stories.content
                    reused_artifacts["reused_stories_id"] = best_stories.document_id
                    skip_stages.append("story_creation")
                    logger.info("reusing_stories", document_id=best_stories.document_id)

        # Update to analyzing immediately
        request.status = RequestStatus.ANALYZING
        await self.state.update_request(request)
        await self.events.emit("request.status_changed", {
            "request_id": request_id, "status": "analyzing",
        })

        # Run the workflow in the background
        task = asyncio.create_task(
            self._run_workflow(request, description, skip_stages, reused_artifacts)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return request

    async def _run_workflow(
        self, request: Request, description: str,
        skip_stages: list[str] | None = None, reused_artifacts: dict[str, Any] | None = None,
    ) -> None:
        """Execute the workflow pipeline in the background."""
        request_id = request.request_id
        try:
            # Brief pause to let the API response return first
            await asyncio.sleep(0.5)

            # Move to in_progress
            request.status = RequestStatus.IN_PROGRESS
            await self.state.update_request(request)
            await self.events.emit("request.status_changed", {
                "request_id": request_id, "status": "in_progress",
            })

            workflow = self.workflow_loader.get_workflow_for_trigger(request.task_type)
            if not workflow:
                raise ValueError(f"No workflow found for trigger: {request.task_type}")

            # Merge reused documents into initial artifacts
            initial_artifacts = {"description": description}
            if reused_artifacts:
                initial_artifacts.update(reused_artifacts)

            result = await self.runner.run(
                workflow, request_id, initial_artifacts,
                skip_stages=skip_stages or []
            )

            # Check if pipeline was escalated (max rework cycles exceeded)
            # Re-fetch the request from DB so we don't overwrite fields that
            # handlers (e.g., _handle_publish) may have persisted during the run.
            fresh_request = await self.state.get_request(request_id) or request

            if result.get("escalation_reason"):
                fresh_request.status = RequestStatus.FAILED
                fresh_request.completed_at = datetime.utcnow()
                await self.state.update_request(fresh_request)
                rework_cycles = result.get("rework_cycle", 0)
                error_msg = f"Pipeline failed after {rework_cycles} rework cycles. Quality gates did not pass."
                await self.events.emit("request.failed", {
                    "request_id": request_id,
                    "error": error_msg,
                    "escalation_reason": result["escalation_reason"][:300],
                })
                logger.warning("request_escalated_failed", request_id=request_id, cycles=rework_cycles)
            else:
                # Check if any subtasks had errors
                subtasks = await self.state.get_subtasks_for_request(request_id)
                failed_subtasks = [s for s in subtasks if s.status == "failed"]

                if failed_subtasks:
                    fresh_request.status = RequestStatus.FAILED
                    fresh_request.completed_at = datetime.utcnow()
                    await self.state.update_request(fresh_request)
                    failed_agents = [s.agent_id for s in failed_subtasks]
                    await self.events.emit("request.failed", {
                        "request_id": request_id,
                        "error": f"Agent failures: {', '.join(failed_agents)}",
                    })
                else:
                    fresh_request.status = RequestStatus.COMPLETED
                    fresh_request.completed_at = datetime.utcnow()
                    await self.state.update_request(fresh_request)
                    await self.events.emit("request.completed", {
                        "request_id": request_id, "result": str(result)[:200],
                    })
                    logger.info("request_completed", request_id=request_id)

        except Exception as e:
            logger.exception("request_failed", request_id=request_id)
            request.status = RequestStatus.FAILED
            await self.state.update_request(request)
            await self.events.emit("request.failed", {
                "request_id": request_id, "error": str(e),
            })

    async def execute_agent(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single agent task. Called by WorkflowRunner."""
        subtask_id = f"{request_id}-{agent_id.upper()}-{uuid.uuid4().hex[:4]}"
        subtask = Subtask(
            subtask_id=subtask_id,
            request_id=request_id,
            agent_id=agent_id,
        )
        await self.state.create_subtask(subtask)
        await self.events.emit("agent.started", {
            "request_id": request_id,
            "agent_id": agent_id,
            "subtask_id": subtask_id,
            "display_name": self.config.agents.get(agent_id, {}).get("display_name", agent_id),
        })

        try:
            subtask.status = SubtaskStatus.IN_PROGRESS
            subtask.started_at = datetime.utcnow()
            await self.state.update_subtask(subtask)

            # Use real agent executor if available, otherwise simulate with delay
            if self._agent_executor:
                # Look up the request to determine which provider to use
                req = await self.state.get_request(request_id)
                provider = req.provider if req else "anthropic"
                result = await self._agent_executor.execute(
                    agent_id, request_id, inputs, provider=provider,
                )
            else:
                result = await self._mock_execute(agent_id, request_id, inputs)

            subtask.status = SubtaskStatus.COMPLETED
            subtask.completed_at = datetime.utcnow()
            subtask.output_artifacts = result.get("artifacts", [])
            # Save the full text output — prefer raw text, fall back to outputs dict
            subtask.output_text = result.get("text", "")
            if not subtask.output_text:
                outputs = result.get("outputs", {})
                subtask.output_text = "\n\n".join(str(v) for v in outputs.values() if v)
            await self.state.update_subtask(subtask)

            # Record token usage for cost tracking
            input_tokens = result.get("input_tokens", 0)
            output_tokens = result.get("output_tokens", 0)
            if input_tokens > 0 or output_tokens > 0:
                model = self.config.agents.get(agent_id, {}).get("model", "claude-sonnet-4-6")
                await self._token_tracker.record(
                    request_id=request_id,
                    subtask_id=subtask_id,
                    agent_id=agent_id,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            # Layer 3: Parse stories and track status through pipeline
            if agent_id == "user_story_author" and subtask.output_text:
                await self._parse_and_save_stories(request_id, subtask.output_text)
            elif agent_id in ("backend_specialist", "frontend_specialist"):
                await self._update_story_statuses(request_id, agent_id, StoryStatus.IN_PROGRESS)
            elif agent_id == "code_reviewer":
                await self._update_story_statuses(request_id, agent_id, StoryStatus.REVIEW)
            elif agent_id == "tester_specialist":
                await self._update_story_statuses(request_id, agent_id, StoryStatus.TESTING)
                if subtask.output_text:
                    await self._parse_and_save_test_cases(request_id, subtask.output_text)
            elif agent_id == "devops_specialist":
                await self._update_story_statuses(request_id, agent_id, StoryStatus.DONE)

            # Save agent output as a persisted document
            if subtask.output_text and len(subtask.output_text) > 50:
                await self._save_document(request_id, agent_id, subtask.output_text)

            await self.events.emit("agent.completed", {
                "request_id": request_id,
                "agent_id": agent_id,
                "subtask_id": subtask_id,
                "display_name": self.config.agents.get(agent_id, {}).get("display_name", agent_id),
                "artifacts": subtask.output_artifacts,
            })

            # Store the output in the result dict keyed by agent role
            # so downstream agents and quality gates can find it
            result[f"{agent_id}_output"] = subtask.output_text
            if agent_id == "code_reviewer":
                result["review_report"] = subtask.output_text
            return result

        except Exception as e:
            subtask.status = SubtaskStatus.FAILED
            subtask.completed_at = datetime.utcnow()
            subtask.error_message = str(e)
            await self.state.update_subtask(subtask)
            await self.events.emit("agent.failed", {
                "request_id": request_id, "agent_id": agent_id, "error": str(e),
            })
            return {"status": "failed", "error": str(e), "outputs": {}, "artifacts": []}

    # ── Code Commit Handler ────────────────────────

    async def _handle_code_commit(self, request_id: str, artifacts: dict[str, Any]) -> dict[str, Any]:
        """Parse agent code outputs, write to disk, compile, test, git push."""
        from src.core.code_writer import CodeWriteError

        await self.events.emit("code_commit.started", {"request_id": request_id})
        logger.info("code_commit_started", request_id=request_id)

        # Collect code outputs from backend and frontend specialists
        agent_outputs: dict[str, str] = {}
        for key, value in artifacts.items():
            if isinstance(value, str) and ("backend_specialist" in key or "backend_code" in key):
                agent_outputs["backend_specialist"] = value
            if isinstance(value, str) and ("frontend_specialist" in key or "frontend_code" in key):
                agent_outputs["frontend_specialist"] = value

        if not agent_outputs:
            logger.warning("no_code_outputs_for_commit", request_id=request_id)
            return {"commit_status": "skipped", "reason": "No code outputs found"}

        try:
            description = artifacts.get("description", request_id)[:80]
            dep_state = await self._code_writer.commit_code(request_id, description, agent_outputs)

            # Persist artifact metadata on the Request so the UI can display it after page reload
            try:
                req = await self.state.get_request(request_id)
                if req:
                    req.published_files = dep_state.files_committed or []
                    req.commit_sha = dep_state.commit_sha
                    # Build a GitHub commit URL from the configured repo + sha if available
                    import os as _os
                    repo = _os.getenv("GITHUB_REPO", "")
                    if repo and dep_state.commit_sha:
                        req.commit_url = f"https://github.com/{repo}/commit/{dep_state.commit_sha}"
                    await self.state.update_request(req)
            except Exception as e:
                logger.warning("code_commit_persist_failed", request_id=request_id, error=str(e))

            await self.events.emit("code_commit.completed", {
                "request_id": request_id,
                "commit_sha": dep_state.commit_sha,
                "files": dep_state.files_committed,
            })

            return {
                "commit_sha": dep_state.commit_sha,
                "files_committed": dep_state.files_committed,
                "deployment_id": dep_state.deployment_id,
                "commit_status": "success",
            }

        except CodeWriteError as e:
            await self.events.emit("code_commit.failed", {
                "request_id": request_id, "error": str(e),
            })
            logger.error("code_commit_failed", request_id=request_id, error=str(e))
            raise RuntimeError(f"Code commit failed: {e}")

    async def _handle_publish(self, request_id: str, artifacts: dict[str, Any]) -> dict[str, Any]:
        """Publish research artifacts to docs/research/ and GitHub. Soft-fails on errors."""
        await self.events.emit("research_publish.started", {"request_id": request_id})
        logger.info("research_publish_started", request_id=request_id)

        description = artifacts.get("description", request_id)
        result = await self._research_publisher.publish(request_id, description, artifacts)

        # Persist artifact metadata on the Request so the UI can display it after page reload
        try:
            req = await self.state.get_request(request_id)
            if req:
                req.published_files = result.get("published_files", []) or []
                req.commit_sha = result.get("commit_sha")
                req.commit_url = result.get("commit_url")
                await self.state.update_request(req)
        except Exception as e:
            logger.warning("publish_persist_failed", request_id=request_id, error=str(e))

        if result.get("publish_error"):
            await self.events.emit("research_publish.partial", {
                "request_id": request_id,
                "error": result["publish_error"],
                "files": result.get("published_files", []),
            })
            logger.warning(
                "research_publish_partial",
                request_id=request_id,
                error=result["publish_error"],
            )
        else:
            await self.events.emit("research_publish.completed", {
                "request_id": request_id,
                "commit_sha": result.get("commit_sha"),
                "commit_url": result.get("commit_url"),
                "files": result.get("published_files", []),
            })
            logger.info(
                "research_published",
                request_id=request_id,
                commit_sha=result.get("commit_sha"),
                files=len(result.get("published_files", [])),
            )

        return result

    # ── Document Persistence ───────────────────────

    AGENT_DOC_TYPE_MAP = {
        "prd_specialist": "prd",
        "user_story_author": "user_stories",
        "backend_specialist": "backend_code",
        "frontend_specialist": "frontend_code",
        "code_reviewer": "code_review",
        "tester_specialist": "test_report",
        "devops_specialist": "deploy_report",
        "research_specialist": "research_report",
        "content_creator": "content_artifact",
    }

    async def _save_document(self, request_id: str, agent_id: str, content: str) -> None:
        """Save agent output as a persisted document in the knowledge base."""
        doc_type = self.AGENT_DOC_TYPE_MAP.get(agent_id)
        if not doc_type:
            return
        try:
            # Extract title from first heading or first line
            title = request_id
            for line in content.split("\n"):
                line = line.strip().lstrip("#").strip()
                if line and len(line) > 5:
                    title = line[:150]
                    break

            tags = self._extract_keywords(content[:1000])

            doc = Document(
                document_id=f"doc-{uuid.uuid4().hex[:8]}",
                request_id=request_id,
                doc_type=doc_type,
                title=title,
                content=content,
                agent_id=agent_id,
                tags=tags,
            )
            await self.state.save_document(doc)
            logger.info("document_saved", doc_type=doc_type, request_id=request_id, title=title[:50])
        except Exception as e:
            logger.warning("document_save_failed", error=str(e), agent_id=agent_id)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract searchable keywords from text content."""
        # Remove markdown formatting
        clean = re.sub(r'[#*`\[\]()_\-|]', ' ', text.lower())
        # Split into words
        words = clean.split()
        # Filter: length > 3, not common stopwords
        stopwords = {
            'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was', 'were',
            'been', 'have', 'has', 'will', 'would', 'could', 'should', 'must', 'shall',
            'not', 'but', 'all', 'each', 'every', 'some', 'any', 'when', 'where', 'what',
            'which', 'who', 'how', 'than', 'then', 'into', 'also', 'only', 'more', 'most',
            'just', 'about', 'above', 'below', 'between', 'does', 'done', 'such', 'they',
            'them', 'their', 'there', 'these', 'those', 'being', 'both', 'same', 'other',
        }
        keywords = list(dict.fromkeys(  # preserve order, deduplicate
            w for w in words if len(w) > 3 and w not in stopwords and w.isalpha()
        ))
        return keywords[:20]  # Cap at 20 tags

    async def _parse_and_save_stories(self, request_id: str, output_text: str) -> None:
        """Parse user stories from the User Story Author's output and save to DB."""
        try:
            stories = self._extract_stories(request_id, output_text)
            # Split output into per-story blocks for AC extraction
            story_blocks = re.split(r'(?=###\s+(?:Story:?\s*)?(?:US-|\[US-))', output_text)
            block_map: dict[str, str] = {}
            for block in story_blocks:
                id_match = re.search(r'(US-\d+(?:-\d+)?)', block)
                if id_match:
                    block_map[id_match.group(1)] = block

            for story in stories:
                await self.state.create_story(story)
                # Parse and save acceptance criteria for this story
                block_text = block_map.get(story.story_id, story.description)
                acs = self._extract_acceptance_criteria(story.story_id, block_text)
                for ac in acs:
                    await self.state.create_acceptance_criterion(ac)
                await self.events.emit("story.created", {
                    "request_id": request_id,
                    "story_id": story.story_id,
                    "title": story.title,
                    "status": story.status,
                    "acceptance_criteria_count": len(acs),
                })
            logger.info("stories_parsed", request_id=request_id, count=len(stories))
        except Exception as e:
            logger.warning("story_parsing_failed", request_id=request_id, error=str(e))

    def _extract_stories(self, request_id: str, text: str) -> list[Story]:
        """Extract individual stories from User Story Author output."""
        stories: list[Story] = []
        # Pattern 1: ### US-XXX or ### Story: US-XXX
        story_blocks = re.split(r'(?=###\s+(?:Story:?\s*)?(?:US-|\[US-))', text)
        counter = 1

        for block in story_blocks:
            block = block.strip()
            if not block:
                continue

            # Extract story ID from the block
            id_match = re.search(r'(US-\d+(?:-\d+)?)', block)
            story_id = id_match.group(1) if id_match else f"US-{request_id[-6:]}-{counter:03d}"

            # Extract title — first line after ###
            title_match = re.search(r'###\s+(?:Story:?\s*)?(?:\[?US-\d+(?:-\d+)?\]?\s*)?(.+)', block)
            title = title_match.group(1).strip().strip("*").strip() if title_match else f"Story {counter}"
            if not title or title == story_id:
                # Try next non-empty line
                lines = [l.strip() for l in block.split("\n") if l.strip() and not l.strip().startswith("#")]
                title = lines[0][:100] if lines else f"Story {counter}"

            # Extract priority
            priority = None
            pri_match = re.search(r'[Pp]riority:?\s*(Critical|High|Medium|Low)', block, re.IGNORECASE)
            if pri_match:
                priority = pri_match.group(1).lower()

            # Get the full story text as description
            description = block[:2000]

            stories.append(Story(
                story_id=story_id,
                request_id=request_id,
                title=title[:200],
                description=description,
                status=StoryStatus.TODO,
                priority=priority,
            ))
            counter += 1

        # If no ### patterns found, try simpler formats (bullet lists, numbered lists)
        if not stories:
            lines = text.strip().split("\n")
            for line in lines:
                line = line.strip()
                # Match: - US-001: Title or 1. Title or - **Title**
                match = re.match(r'(?:[-*]\s+|(?:\d+\.)\s+)(?:(US-\d+(?:-\d+)?)[:\s]+)?(.+)', line)
                if match and len(match.group(2).strip()) > 5:
                    sid = match.group(1) or f"US-{request_id[-6:]}-{counter:03d}"
                    title = match.group(2).strip().strip("*").strip()
                    stories.append(Story(
                        story_id=sid,
                        request_id=request_id,
                        title=title[:200],
                        description=line,
                        status=StoryStatus.TODO,
                    ))
                    counter += 1

        return stories

    def _extract_acceptance_criteria(
        self, story_id: str, block_text: str
    ) -> list[AcceptanceCriterion]:
        """Extract Given/When/Then acceptance criteria from a story text block."""
        criteria: list[AcceptanceCriterion] = []

        # Find the "Acceptance Criteria:" section
        ac_match = re.search(
            r'\*{0,2}Acceptance\s+Criteria:?\*{0,2}\s*\n(.*?)(?:\n\*{0,2}(?:Notes|Priority|Effort|Traces|---)|$)',
            block_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not ac_match:
            return criteria

        ac_section = ac_match.group(1)

        # Split into individual criteria (bullet points starting with - or *)
        bullets = re.findall(r'[-*]\s+(.+?)(?=\n[-*]\s+|\Z)', ac_section, re.DOTALL)

        for idx, bullet in enumerate(bullets, start=1):
            text = bullet.strip().replace("\n", " ").strip()
            if not text:
                continue

            # Parse Given / When / Then clauses
            given, when, then = "", "", ""
            gwt = re.match(
                r'[Gg]iven\s+(.+?),?\s+[Ww]hen\s+(.+?),?\s+[Tt]hen\s+(.+)',
                text,
            )
            if gwt:
                given = gwt.group(1).strip().rstrip(",")
                when = gwt.group(2).strip().rstrip(",")
                then = gwt.group(3).strip().rstrip(".")
            else:
                # Try alternate format: just "When X, Then Y" (no Given)
                wt = re.match(r'[Ww]hen\s+(.+?),?\s+[Tt]hen\s+(.+)', text)
                if wt:
                    when = wt.group(1).strip().rstrip(",")
                    then = wt.group(2).strip().rstrip(".")

            ac_id = f"{story_id}-AC-{idx:02d}"
            criteria.append(AcceptanceCriterion(
                ac_id=ac_id,
                story_id=story_id,
                criterion_text=text,
                given_clause=given,
                when_clause=when,
                then_clause=then,
            ))

        return criteria

    async def _parse_and_save_test_cases(
        self, request_id: str, output_text: str
    ) -> None:
        """Parse test cases from Tester output, link to stories, and extract coverage."""
        try:
            stories = await self.state.get_stories_for_request(request_id)
            story_map = {s.story_id: s for s in stories}

            test_cases = self._extract_test_cases(output_text, story_map)

            # Group TCs by story for coverage calculation
            story_tc_stats: dict[str, dict[str, int]] = {}  # story_id -> {total, passed}
            for tc in test_cases:
                await self.state.create_test_case(tc)
                stats = story_tc_stats.setdefault(tc.story_id, {"total": 0, "passed": 0})
                stats["total"] += 1
                if tc.status == "pass":
                    stats["passed"] += 1

            # SBD-03: Update coverage_pct per story based on pass rate
            for story_id, stats in story_tc_stats.items():
                if story_id in story_map and stats["total"] > 0:
                    story = story_map[story_id]
                    story.coverage_pct = round(stats["passed"] / stats["total"] * 100, 1)
                    await self.state.update_story(story)

            logger.info(
                "test_cases_parsed",
                request_id=request_id,
                count=len(test_cases),
                stories_with_coverage=len(story_tc_stats),
            )
        except Exception as e:
            logger.warning("test_case_parsing_failed", request_id=request_id, error=str(e))

    def _extract_test_cases(
        self, text: str, story_map: dict[str, Story]
    ) -> list[TestCase]:
        """Extract test cases from Tester markdown table output."""
        test_cases: list[TestCase] = []

        # Match table rows: | TC-XXX | name | US-XXX AC-X | type | status | notes |
        row_pattern = re.compile(
            r'\|\s*(TC-\d+)\s*\|\s*(.+?)\s*\|\s*(US-\S+)\s*(AC-\d+)?\s*\|\s*\w+\s*\|\s*'
            r'(.+?)\s*\|\s*.*?\|'
        )
        for match in row_pattern.finditer(text):
            tc_id = match.group(1)
            tc_name = match.group(2).strip()
            story_ref = match.group(3).strip()
            status_raw = match.group(5).strip()

            # Normalize status
            status = "pending"
            if "PASS" in status_raw.upper() or "\u2705" in status_raw:
                status = "pass"
            elif "FAIL" in status_raw.upper() or "\u274c" in status_raw:
                status = "fail"
            elif "SKIP" in status_raw.upper() or "\u26a0" in status_raw:
                status = "pending"

            # Resolve story_id — try exact match, then with request prefix
            story_id = story_ref
            if story_id not in story_map:
                # Try matching by suffix (e.g., "US-001" might map to "US-001-001")
                for sid in story_map:
                    if sid.startswith(story_ref) or sid.endswith(story_ref.replace("US-", "")):
                        story_id = sid
                        break

            # Only create TC if we have a valid story to link to
            if story_id in story_map:
                test_cases.append(TestCase(
                    test_id=tc_id,
                    story_id=story_id,
                    name=tc_name,
                    status=status,
                    last_run_at=datetime.utcnow() if status in ("pass", "fail") else None,
                ))

        # Fallback: try simpler line-based format if no table rows found
        if not test_cases:
            line_pattern = re.compile(
                r'(TC-\d+)\s*[:\|]\s*(.+?)\s*[:\|]\s*(US-\S+).*?(PASS|FAIL|SKIP)',
                re.IGNORECASE,
            )
            for match in line_pattern.finditer(text):
                tc_id = match.group(1)
                tc_name = match.group(2).strip()
                story_ref = match.group(3).strip()
                status = match.group(4).strip().lower()
                if status == "skip":
                    status = "pending"

                story_id = story_ref
                if story_id not in story_map:
                    for sid in story_map:
                        if sid.startswith(story_ref):
                            story_id = sid
                            break

                if story_id in story_map:
                    test_cases.append(TestCase(
                        test_id=tc_id,
                        story_id=story_id,
                        name=tc_name,
                        status=status,
                        last_run_at=datetime.utcnow() if status in ("pass", "fail") else None,
                    ))

        return test_cases

    async def _update_story_statuses(
        self, request_id: str, agent_id: str, new_status: StoryStatus
    ) -> None:
        """Update story statuses based on which agent completed work."""
        stories = await self.state.get_stories_for_request(request_id)
        for story in stories:
            if story.status != StoryStatus.DONE:
                story.status = new_status
                if not story.assigned_agent:
                    story.assigned_agent = agent_id
                await self.state.update_story(story)

    async def _mock_execute(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Simulate agent execution with realistic delays and mock outputs."""
        delay_range = MOCK_AGENT_DELAYS.get(agent_id, (2.0, 5.0))
        delay = random.uniform(*delay_range)

        logger.info("mock_agent_working", agent_id=agent_id, delay=f"{delay:.1f}s")

        # Emit progress events during the simulated work
        steps = max(2, int(delay / 1.5))
        for i in range(steps):
            await asyncio.sleep(delay / steps)
            await self.events.emit("agent.progress", {
                "request_id": request_id,
                "agent_id": agent_id,
                "progress": round((i + 1) / steps * 100),
                "display_name": self.config.agents.get(agent_id, {}).get("display_name", agent_id),
                "message": _mock_progress_message(agent_id, i, steps),
            })

        # Generate mock output based on agent role
        mock_output = _mock_agent_output(agent_id, request_id, inputs)
        return {
            "status": "completed",
            "outputs": mock_output,
            "artifacts": mock_output.get("artifacts", []),
        }


def _mock_progress_message(agent_id: str, step: int, total: int) -> str:
    """Generate a realistic progress message for mock execution."""
    messages: dict[str, list[str]] = {
        "engineering_lead": ["Analyzing request...", "Decomposing into subtasks...", "Creating delegation plan..."],
        "prd_specialist": ["Gathering requirements...", "Writing PRD sections...", "Defining acceptance criteria...", "Finalizing document..."],
        "user_story_author": ["Creating user stories...", "Writing acceptance criteria...", "Assigning priorities..."],
        "code_reviewer": ["Setting up feature branch...", "Reviewing code structure...", "Checking code quality...", "Posting review comments..."],
        "backend_specialist": ["Implementing API endpoints...", "Writing database models...", "Creating unit tests...", "Running test suite..."],
        "frontend_specialist": ["Building React components...", "Styling with Tailwind...", "Adding interactions...", "Writing component tests..."],
        "devops_specialist": ["Configuring deployment...", "Running smoke tests...", "Verifying health checks..."],
        "tester_specialist": ["Writing E2E tests...", "Executing test suite...", "Generating coverage report..."],
    }
    agent_messages = messages.get(agent_id, ["Processing..."])
    idx = min(step, len(agent_messages) - 1)
    return agent_messages[idx]


def _mock_agent_output(agent_id: str, request_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Generate realistic mock output for each agent type."""
    desc = inputs.get("description", "")[:80]
    outputs: dict[str, dict[str, Any]] = {
        "engineering_lead": {
            "delegation_plan": f"Delegated {request_id}: planning → development → delivery",
        },
        "prd_specialist": {
            "prd_document": f"# PRD: {desc}\n\n## Requirements\n- REQ-001: Core functionality\n- REQ-002: User interface\n- REQ-003: Testing",
            "artifacts": [f"docs/prd/{request_id}.md"],
        },
        "user_story_author": {
            "user_stories": f"## Stories for {request_id}\n- US-001: Implement core feature\n- US-002: Build UI components\n- US-003: Write tests",
            "artifacts": [f"docs/stories/{request_id}.md"],
        },
        "code_reviewer": {
            "review_report": f"Code review for {request_id}: All checks passed. Coverage: 85%",
        },
        "backend_specialist": {
            "backend_code": f"Implemented API endpoints for {request_id}",
            "artifacts": [f"src/api/{request_id.lower()}.py", f"tests/test_{request_id.lower()}.py"],
        },
        "frontend_specialist": {
            "frontend_code": f"Built React components for {request_id}",
            "artifacts": [f"frontend/src/pages/{request_id}.tsx"],
        },
        "devops_specialist": {
            "deployment_report": f"Deployed {request_id} to staging → production. Health checks passed.",
        },
        "tester_specialist": {
            "test_report": (
                f"## Test Results\n\n"
                f"| # | Test Case | Traces To | Type | Status | Notes |\n"
                f"|---|-----------|-----------|------|--------|-------|\n"
                f"| TC-001 | Core feature validation | US-001 AC-1 | Unit | ✅ PASS | — |\n"
                f"| TC-002 | Input validation checks | US-001 AC-2 | Unit | ✅ PASS | — |\n"
                f"| TC-003 | Error handling paths | US-001 AC-3 | Unit | ✅ PASS | — |\n"
                f"| TC-004 | UI component rendering | US-002 AC-1 | Integration | ✅ PASS | — |\n"
                f"| TC-005 | Form submission flow | US-002 AC-2 | Integration | ✅ PASS | — |\n"
                f"| TC-006 | Negative test scenario | US-002 AC-3 | E2E | ❌ FAIL | Edge case timeout |\n"
                f"| TC-007 | Test suite coverage | US-003 AC-1 | Unit | ✅ PASS | — |\n\n"
                f"## Summary\n\n"
                f"| Metric | Value |\n"
                f"|--------|-------|\n"
                f"| Total | 7 |\n"
                f"| Passed | 6 ✅ |\n"
                f"| Failed | 1 ❌ |\n"
                f"| Pass Rate | 86% |\n\n"
                f"## Verdict\n**NEEDS FIXES** — 1 test failing on edge case timeout in form submission"
            ),
            "artifacts": [f"tests/e2e/{request_id.lower()}_test.py"],
        },
    }
    return outputs.get(agent_id, {"output": f"Completed by {agent_id}"})
