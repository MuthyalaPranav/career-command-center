from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .match_models import ResumeSource, Suggestion
from .resume_extraction_models import PdfLayout

GenerationMode = Literal["ats", "original_layout"]


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_id: UUID
    match_id: UUID
    mode: GenerationMode = "ats"


class LayoutIssue(BaseModel):
    suggestion_id: UUID
    original_text: str
    suggested_replacement: str
    reason: str


class LayoutValidation(BaseModel):
    page_count: int = Field(gt=0)
    edited_pages: list[int]
    visually_compared_pages: int = Field(gt=0)
    unexpected_visual_changes: bool = False
    links_and_annotations_preserved: bool = True
    unaffected_text_preserved: bool = True
    warnings: list[str] = Field(default_factory=list)


class LayoutCompatibility(BaseModel):
    compatible: bool
    message: str
    approved_suggestion_count: int = Field(ge=0)
    original_preview_available: bool = False
    issues: list[LayoutIssue] = Field(default_factory=list)
    validation: LayoutValidation | None = None


class GeneratedResume(GenerationRequest):
    generation_id: UUID
    status: Literal["completed"] = "completed"
    page_count: int = Field(gt=0)
    approved_suggestion_count: int = Field(ge=0)
    applied_suggestion_count: int = Field(ge=0)
    skipped_suggestion_count: int = Field(ge=0)
    validation: LayoutValidation | None = None


class VersionContent(BaseModel):
    sections: dict[str, list[str]]
    name: str = ""
    contact: list[str] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)


class VersionSnapshot(BaseModel):
    # Legacy PDF text can be indexed even when the original JD or approval trail is gone.
    content: VersionContent | None
    job_id: UUID | None = None
    job_title: str | None = None
    keywords: list[str] | None = None
    applied_suggestions: list[Suggestion] | None = None
    created_at: datetime | None = None


class StoredGeneration(GeneratedResume):
    snapshot: VersionSnapshot | None = None


class ParagraphFormat(BaseModel):
    text: str
    style: str | None = None


class SourceFormatting(BaseModel):
    paragraphs: list[ParagraphFormat] = Field(default_factory=list)


class GenerationSource(ResumeSource):
    raw_text: str = ""
    extraction_metadata: SourceFormatting | None = None


class LayoutSource(GenerationSource):
    layout_metadata: PdfLayout | None = None
