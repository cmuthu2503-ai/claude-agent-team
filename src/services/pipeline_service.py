"""Pipeline service for automated task handoffs between agents."""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from uuid import uuid4

from src.models.pipeline import (
    PipelineRelationship, PipelineType, PipelineStatus, 
    ResearchData, ContentOutputType, PipelineConfig
)
from src.models.base import Task, TaskStatus, AgentType, TaskPriority
from src.state.store import StateStore
from src.config.loader import load_config

logger = logging.getLogger(__name__)


class PipelineServiceError(Exception):
    """Base exception for pipeline service errors."""
    pass


class PipelineService:
    """Service for managing automated task pipelines."""
    
    def __init__(self, state_store: StateStore):
        self.state_store = state_store
        self.config = self._load_pipeline_config()
        # Task service will be injected to avoid circular imports
        self._task_service = None
        
    def set_task_service(self, task_service):
        """Set task service reference (dependency injection)."""
        self._task_service = task_service
        
    def _load_pipeline_config(self) -> PipelineConfig:
        """Load pipeline configuration from YAML."""
        try:
            config_data = load_config("pipeline.yaml")
            return PipelineConfig(**config_data)
        except Exception as e:
            logger.warning(f"Failed to load pipeline config, using defaults: {e}")
            return PipelineConfig()
    
    async def trigger_research_to_content(self, research_task: Task) -> Optional[PipelineRelationship]:
        """
        Trigger Content Creator task creation when Research task completes.
        
        Args:
            research_task: Completed research task
            
        Returns:
            PipelineRelationship if successful, None if skipped
            
        Raises:
            PipelineServiceError: If pipeline creation fails
        """
        if not self.config.enabled_globally:
            logger.info(f"Pipeline disabled globally, skipping task {research_task.id}")
            return None
            
        if research_task.pipeline_disabled:
            logger.info(f"Pipeline disabled for task {research_task.id}")
            return None
            
        if research_task.agent_type != AgentType.RESEARCH:
            logger.warning(f"Task {research_task.id} is not assigned to Research AI Agent")
            return None
            
        if research_task.status != TaskStatus.COMPLETED:
            logger.warning(f"Task {research_task.id} is not completed, status: {research_task.status}")
            return None
            
        # Check if pipeline relationship already exists
        existing = await self._get_pipeline_by_parent_id(research_task.id)
        if existing:
            logger.info(f"Pipeline already exists for task {research_task.id}")
            return existing
            
        try:
            # Create pipeline relationship
            relationship = PipelineRelationship(
                parent_task_id=research_task.id,
                child_task_id=0,  # Will be updated when child task is created
                pipeline_type=PipelineType.RESEARCH_TO_CONTENT,
                status=PipelineStatus.PENDING
            )
            
            # Save relationship to get ID
            relationship_id = await self._save_pipeline_relationship(relationship)
            relationship.id = relationship_id
            
            # Create Content Creator task
            content_task = await self._create_content_creator_task(research_task, relationship)
            
            # Update relationship with child task ID
            relationship.child_task_id = content_task.id
            relationship.status = PipelineStatus.COMPLETED
            relationship.completed_at = datetime.utcnow()
            await self._update_pipeline_relationship(relationship)
            
            logger.info(f"Successfully created pipeline: Research {research_task.id} -> Content {content_task.id}")
            return relationship
            
        except Exception as e:
            logger.error(f"Failed to create pipeline for task {research_task.id}: {e}")
            # Update relationship status to failed
            if 'relationship' in locals():
                relationship.status = PipelineStatus.FAILED
                relationship.error_message = str(e)
                await self._update_pipeline_relationship(relationship)
            raise PipelineServiceError(f"Pipeline creation failed: {e}")
    
    async def _create_content_creator_task(self, research_task: Task, relationship: PipelineRelationship) -> Task:
        """Create Content Creator task with research data transfer."""
        # Extract research data
        research_data = await self._extract_research_data(research_task)
        
        # Determine output type based on research type
        output_type = self._determine_output_type(research_data.research_type)
        
        # Create content creator task
        content_task_data = {
            "title": f"Generate Content: {research_task.title}",
            "description": self._build_content_task_description(research_task, research_data, output_type),
            "agent_type": AgentType.CONTENT_CREATOR,
            "priority": research_task.priority,  # Inherit priority
            "status": TaskStatus.TODO,
            "output_type": output_type.value,
            "created_by": research_task.created_by,
            "metadata": {
                "source_task_id": research_task.id,
                "pipeline_relationship_id": relationship.id,
                "research_data": research_data.model_dump(),
                "auto_generated": True,
                "content_output_type": output_type.value
            }
        }
        
        content_task = await self._task_service.create_task(content_task_data)
        
        # Attempt to assign to Content Creator agent with retry logic
        await self._assign_with_retry(content_task, AgentType.CONTENT_CREATOR, relationship)
        
        return content_task
    
    async def _assign_with_retry(self, task: Task, agent_type: AgentType, relationship: PipelineRelationship):
        """Assign task to agent with retry logic and exponential backoff."""
        for attempt in range(self.config.max_retries):
            try:
                # Try to assign to available agent
                success = await self._task_service.assign_to_available_agent(task.id, agent_type)
                if success:
                    logger.info(f"Successfully assigned task {task.id} to {agent_type} on attempt {attempt + 1}")
                    return
                else:
                    logger.warning(f"No available {agent_type} agent for task {task.id}, attempt {attempt + 1}")
                    
            except Exception as e:
                logger.error(f"Assignment attempt {attempt + 1} failed for task {task.id}: {e}")
            
            # Update retry count
            relationship.retry_count = attempt + 1
            await self._update_pipeline_relationship(relationship)
            
            # Wait with exponential backoff
            if attempt < self.config.max_retries - 1:
                wait_time = self.config.retry_delay_seconds * (2 ** attempt)
                logger.info(f"Waiting {wait_time}s before retry {attempt + 2}")
                await asyncio.sleep(wait_time)
        
        # All retries failed
        relationship.status = PipelineStatus.FAILED
        relationship.error_message = f"Failed to assign task after {self.config.max_retries} attempts"
        await self._update_pipeline_relationship(relationship)
        
        logger.error(f"Failed to assign task {task.id} after {self.config.max_retries} attempts")
        # Note: We don't raise an exception here to allow the content task to remain unassigned
        # for manual intervention
    
    async def _extract_research_data(self, research_task: Task) -> ResearchData:
        """Extract research findings and metadata from completed task."""
        # In a real implementation, this would parse task outputs, attachments, etc.
        # For now, we'll extract from task description and metadata
        
        findings = research_task.description or "Research findings not available"
        sources = []
        metadata = research_task.metadata or {}
        
        # Extract sources from metadata if available
        if "sources" in metadata:
            sources = metadata.get("sources", [])
        
        # Extract research type from metadata or infer from title/description
        research_type = metadata.get("research_type", self._infer_research_type(research_task))
        
        return ResearchData(
            findings=findings,
            sources=sources,
            metadata=metadata,
            confidence_score=metadata.get("confidence_score"),
            research_type=research_type,
            file_attachments=metadata.get("file_attachments", [])
        )
    
    def _infer_research_type(self, research_task: Task) -> str:
        """Infer research type from task title and description."""
        text = f"{research_task.title} {research_task.description or ''}".lower()
        
        if any(keyword in text for keyword in ["market", "industry", "competition", "competitive"]):
            return "market_analysis"
        elif any(keyword in text for keyword in ["competitor", "analysis", "compare"]):
            return "competitor_research"
        elif any(keyword in text for keyword in ["technical", "technology", "implementation"]):
            return "technical_research"
        else:
            return "general_research"
    
    def _determine_output_type(self, research_type: Optional[str]) -> ContentOutputType:
        """Determine content output type based on research type."""
        if not research_type:
            return ContentOutputType.RESEARCH_SUMMARY
            
        return self.config.content_output_mappings.get(
            research_type, 
            ContentOutputType.RESEARCH_SUMMARY
        )
    
    def _build_content_task_description(self, research_task: Task, research_data: ResearchData, output_type: ContentOutputType) -> str:
        """Build description for content creator task."""
        base_desc = f"Generate {output_type.value.replace('_', ' ')} based on research from: {research_task.title}\n\n"
        
        base_desc += f"Research Findings:\n{research_data.findings}\n\n"
        
        if research_data.sources:
            base_desc += f"Sources:\n" + "\n".join(f"- {source}" for source in research_data.sources) + "\n\n"
        
        if research_data.confidence_score:
            base_desc += f"Research Confidence Score: {research_data.confidence_score:.2f}\n\n"
        
        # Add specific instructions based on output type
        if output_type == ContentOutputType.MARKET_REPORT:
            base_desc += "Please create a comprehensive market report including market size, trends, key players, and recommendations."
        elif output_type == ContentOutputType.COMPETITIVE_ANALYSIS:
            base_desc += "Please create a competitive analysis template comparing key competitors, their strengths, weaknesses, and market positioning."
        elif output_type == ContentOutputType.RESEARCH_SUMMARY:
            base_desc += "Please create a concise research summary with key findings and recommendations for further investigation if needed."
        elif output_type == ContentOutputType.TECHNICAL_DOCUMENTATION:
            base_desc += "Please create technical documentation based on the research findings, including implementation details and recommendations."
        else:
            base_desc += "Please create appropriate content based on the research findings provided."
        
        return base_desc
    
    async def get_pipeline_relationships(self, task_id: int) -> List[PipelineRelationship]:
        """Get all pipeline relationships for a task (as parent or child)."""
        async with self.state_store.get_connection() as conn:
            # Get relationships where task is parent or child
            cursor = await conn.execute("""
                SELECT id, parent_task_id, child_task_id, pipeline_type, status, 
                       created_at, completed_at, error_message, retry_count
                FROM task_pipeline 
                WHERE parent_task_id = ? OR child_task_id = ?
            """, (task_id, task_id))
            
            rows = await cursor.fetchall()
            return [
                PipelineRelationship(
                    id=row[0],
                    parent_task_id=row[1],
                    child_task_id=row[2],
                    pipeline_type=PipelineType(row[3]),
                    status=PipelineStatus(row[4]),
                    created_at=datetime.fromisoformat(row[5]),
                    completed_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    error_message=row[7],
                    retry_count=row[8]
                )
                for row in rows
            ]
    
    async def _get_pipeline_by_parent_id(self, parent_task_id: int) -> Optional[PipelineRelationship]:
        """Get pipeline relationship by parent task ID."""
        relationships = await self.get_pipeline_relationships(parent_task_id)
        for rel in relationships:
            if rel.parent_task_id == parent_task_id:
                return rel
        return None
    
    async def _save_pipeline_relationship(self, relationship: PipelineRelationship) -> int:
        """Save pipeline relationship to database."""
        async with self.state_store.get_connection() as conn:
            cursor = await conn.execute("""
                INSERT INTO task_pipeline 
                (parent_task_id, child_task_id, pipeline_type, status, created_at, retry_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                relationship.parent_task_id,
                relationship.child_task_id,
                relationship.pipeline_type.value,
                relationship.status.value,
                relationship.created_at.isoformat(),
                relationship.retry_count
            ))
            await conn.commit()
            return cursor.lastrowid
    
    async def _update_pipeline_relationship(self, relationship: PipelineRelationship):
        """Update existing pipeline relationship."""
        async with self.state_store.get_connection() as conn:
            await conn.execute("""
                UPDATE task_pipeline 
                SET child_task_id = ?, status = ?, completed_at = ?, 
                    error_message = ?, retry_count = ?
                WHERE id = ?
            """, (
                relationship.child_task_id,
                relationship.status.value,
                relationship.completed_at.isoformat() if relationship.completed_at else None,
                relationship.error_message,
                relationship.retry_count,
                relationship.id
            ))
            await conn.commit()
    
    async def retry_failed_pipeline(self, relationship_id: int) -> bool:
        """Retry a failed pipeline relationship."""
        async with self.state_store.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT id, parent_task_id, child_task_id, pipeline_type, status, 
                       created_at, completed_at, error_message, retry_count
                FROM task_pipeline WHERE id = ?
            """, (relationship_id,))
            
            row = await cursor.fetchone()
            if not row:
                return False
            
            relationship = PipelineRelationship(
                id=row[0],
                parent_task_id=row[1],
                child_task_id=row[2],
                pipeline_type=PipelineType(row[3]),
                status=PipelineStatus(row[4]),
                created_at=datetime.fromisoformat(row[5]),
                completed_at=datetime.fromisoformat(row[6]) if row[6] else None,
                error_message=row[7],
                retry_count=row[8]
            )
        
        if relationship.status != PipelineStatus.FAILED:
            return False
        
        if relationship.retry_count >= self.config.max_retries:
            logger.warning(f"Pipeline {relationship_id} has exceeded max retries")
            return False
        
        # Get the original research task
        research_task = await self._task_service.get_task(relationship.parent_task_id)
        if not research_task:
            return False
        
        # Reset status and retry
        relationship.status = PipelineStatus.IN_PROGRESS
        relationship.error_message = None
        await self._update_pipeline_relationship(relationship)
        
        try:
            await self.trigger_research_to_content(research_task)
            return True
        except Exception as e:
            logger.error(f"Retry failed for pipeline {relationship_id}: {e}")
            return False
    
    async def get_pipeline_config(self) -> PipelineConfig:
        """Get current pipeline configuration."""
        return self.config
    
    async def update_pipeline_config(self, config_updates: Dict[str, Any]) -> PipelineConfig:
        """Update pipeline configuration."""
        # Update in-memory config
        for key, value in config_updates.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        
        # In a production system, you might want to persist this to database or file
        logger.info(f"Pipeline configuration updated: {config_updates}")
        return self.config
