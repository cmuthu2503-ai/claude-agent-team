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

from src.models.base import Document, Request, RequestStatus, Story, StoryStatus, Subtask, SubtaskStatus
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
        self.runner = WorkflowRunner(executor=self)
        self._agent_executor: Any = None  # Set by agent system in Phase 3
        self._background_tasks: set[asyncio.Task] = set()

        # Token tracker for cost recording
        from src.core.token_tracker import TokenTracker
        self._token_tracker = TokenTracker(state)

    def set_agent_executor(self, executor: Any) -> None:
        """Inject the real agent executor (set during Phase 3 agent system init)."""
        self._agent_executor = executor

    async def submit(self, description: str, task_type: str = "feature_request",
                     priority: str = "medium", created_by: str = "") -> Request:
        request_id = f"REQ-{uuid.uuid4().hex[:6].upper()}"
        request = Request(
            request_id=request_id,
            description=description,
            task_type=task_type,
            priority=priority,
            created_by=created_by,
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
            if result.get("escalation_reason"):
                request.status = RequestStatus.FAILED
                request.completed_at = datetime.utcnow()
                await self.state.update_request(request)
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
                    request.status = RequestStatus.FAILED
                    request.completed_at = datetime.utcnow()
                    await self.state.update_request(request)
                    failed_agents = [s.agent_id for s in failed_subtasks]
                    await self.events.emit("request.failed", {
                        "request_id": request_id,
                        "error": f"Agent failures: {', '.join(failed_agents)}",
                    })
                else:
                    request.status = RequestStatus.COMPLETED
                    request.completed_at = datetime.utcnow()
                    await self.state.update_request(request)
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
                result = await self._agent_executor.execute(agent_id, request_id, inputs)
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

    # ── Document Persistence ───────────────────────

    AGENT_DOC_TYPE_MAP = {
        "prd_specialist": "prd",
        "user_story_author": "user_stories",
        "backend_specialist": "backend_code",
        "frontend_specialist": "frontend_code",
        "code_reviewer": "code_review",
        "tester_specialist": "test_report",
        "devops_specialist": "deploy_report",
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
            for story in stories:
                await self.state.create_story(story)
                await self.events.emit("story.created", {
                    "request_id": request_id,
                    "story_id": story.story_id,
                    "title": story.title,
                    "status": story.status,
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
            "test_report": f"All tests passed for {request_id}. Coverage: 87%. 0 regressions.",
            "artifacts": [f"tests/e2e/{request_id.lower()}_test.py"],
        },
    }
    return outputs.get(agent_id, {"output": f"Completed by {agent_id}"})
