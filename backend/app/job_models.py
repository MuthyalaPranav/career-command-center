from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_JOB_DESCRIPTION_LENGTH = 30_000

Classification = Literal["required", "preferred", "mentioned", "negated"]
EvidenceField = Literal[
    "job_title", "skill", "experience_requirements", "education_requirements", "responsibilities", "other_qualifications",
]


class JobAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_description: str = Field(strict=True, min_length=1, max_length=MAX_JOB_DESCRIPTION_LENGTH)

    @field_validator("job_description")
    @classmethod
    def require_nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Paste a job description before analyzing.")
        return value


class JobEvidence(BaseModel):
    field: EvidenceField
    value: str
    snippet: str
    line_number: int
    classification: Classification
    matched_text: str | None = None


class JobAnalysisDetails(BaseModel):
    job_title: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    tools_and_technologies: list[str] = Field(default_factory=list)
    experience_requirements: list[str] = Field(default_factory=list)
    education_requirements: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    other_qualifications: list[str] = Field(default_factory=list)
    ats_keywords: list[str] = Field(default_factory=list)
    evidence: list[JobEvidence] = Field(default_factory=list)


class JobAnalysis(JobAnalysisDetails):
    job_id: UUID


class StoredJobAnalysis(JobAnalysis):
    job_description: str
