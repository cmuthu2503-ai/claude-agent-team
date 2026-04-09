"""Pipeline models for task relationships and data transfer."""
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from enum import Enum


class PipelineType(str, Enum):
    """Types of pipeline relationships."""
    RESEARCH_TO_CONTENT = "research_to_content"


class PipelineStatus(str, Enum):
    """Status of pipeline execution."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContentOutputType(str, Enum):
    """Types of content output based on research type."""
    MARKET_REPORT = "market_report"
    COMPETITIVE_ANALYSIS = "competitive_analysis"
    RESEARCH_SUMMARY = "research_summary"
    TECHNICAL_DOCUMENTATION = "technical_documentation"
    CONTENT_TEMPLATE = "content_template"


class ResearchData(BaseModel):
    """Research findings and metadata for transfer to Content Creator."""
    findings: str = Field(..., description="Main research findings")
    sources: List[str] = Field(default_factory=list, description="Research sources and references")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata like confidence scores")
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Overall confidence in research findings")
    research_type: Optional[str] = Field(None, description="Type of research conducted")
    file_attachments: List[str] = Field(default_factory=list, description="Attached files or links")
    
    class Config:
        json_schema_extra = {
            "example": {
                "findings": "Market analysis shows 25% growth in AI automation tools",
                "sources": ["https://example.com/report1", "Industry Survey 2024"],
                "metadata": {"data_points": 150, "survey_size": 500},
                "confidence_score": 0.85,
                "research_type": "market_analysis",
                "file_attachments": ["market_data.xlsx", "competitor_analysis.pdf"]
            }
        }


class PipelineRelationship(BaseModel):
    """Relationship between parent and child tasks in pipeline."""
    id: Optional[int] = None
    parent_task_id: int = Field(..., description="ID of the parent task (Research)")
    child_task_id: int = Field(..., description="ID of the child task (Content Creator)")
    pipeline_type: PipelineType = Field(..., description="Type of pipeline relationship")
    status: PipelineStatus = Field(default=PipelineStatus.PENDING, description="Current pipeline status")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int = Field(default=0, ge=0, le=3, description="Number of retry attempts")


class PipelineConfig(BaseModel):
    """Configuration settings for pipeline behavior."""
    enabled_globally: bool = Field(default=True, description="Enable pipeline globally")
    max_retries: int = Field(default=3, ge=0, le=10, description="Maximum retry attempts")
    retry_delay_seconds: int = Field(default=30, ge=1, le=3600, description="Delay between retries")
    trigger_timeout_seconds: int = Field(default=5, ge=1, le=60, description="Maximum time for pipeline trigger")
    content_output_mappings: Dict[str, ContentOutputType] = Field(
        default_factory=lambda: {
            "market_analysis": ContentOutputType.MARKET_REPORT,
            "competitor_research": ContentOutputType.COMPETITIVE_ANALYSIS,
            "technical_research": ContentOutputType.TECHNICAL_DOCUMENTATION,
            "general_research": ContentOutputType.RESEARCH_SUMMARY
        },
        description="Mapping of research types to content output types"
    )


class PipelineCreateRequest(BaseModel):
    """Request to manually create a pipeline relationship."""
    parent_task_id: int = Field(..., description="ID of the parent task")
    pipeline_type: PipelineType = Field(..., description="Type of pipeline to create")
    force_trigger: bool = Field(default=False, description="Force trigger even if parent not completed")


class PipelineStatusResponse(BaseModel):
    """Response containing pipeline status information."""
    relationship: PipelineRelationship
    parent_task_title: str
    child_task_title: Optional[str] = None
    can_retry: bool = Field(..., description="Whether pipeline can be retried")


class TaskWithPipelineInfo(BaseModel):
    """Task model extended with pipeline relationship information."""
    task_id: int
    title: str
    status: str
    agent_type: str
    pipeline_relationships: List[PipelineRelationship] = Field(default_factory=list)
    is_pipeline_source: bool = Field(default=False, description="Task is source of pipeline")
    is_pipeline_target: bool = Field(default=False, description="Task is target of pipeline")
