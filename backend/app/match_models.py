from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .job_models import JobAnalysis, JobEvidence

ResumeSection = Literal["professional_summary", "technical_skills", "projects", "work_experience"]
Approval = Literal["pending", "accepted", "rejected"]
EvidenceSource = Literal["project", "task", "work_experience"]
ReviewStatus = Literal["already_know", "not_experienced", "review_later"]
ReplacementText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=700)]


class MatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_id: UUID
    job_id: UUID


class ResumeSource(BaseModel):
    resume_id: UUID
    file_type: str
    sections: dict[str, list[str]]


class SourceOption(BaseModel):
    id: UUID
    label: str


class ResumeOption(SourceOption):
    original_filename: str | None


class JobOption(SourceOption):
    company_name: str | None = Field(default=None, exclude_if=lambda value: value is None)


class MatchSources(BaseModel):
    resumes: list[ResumeOption]
    jobs: list[JobOption]


class ResumeEvidence(BaseModel):
    section: ResumeSection
    line_number: int
    text: str
    matched_text: str


class KeywordMatch(BaseModel):
    keyword: str
    priority: Literal["required", "preferred", "mentioned"]
    match_type: Literal["exact", "alias", "contextual", "missing"]
    resume_evidence: list[ResumeEvidence]
    jd_evidence: list[JobEvidence]


class RelevantResumeLine(BaseModel):
    line_number: int
    text: str
    keywords: list[str]


class QualificationReview(BaseModel):
    category: Literal["experience_requirements", "education_requirements", "other_qualifications"]
    requirement: str
    jd_evidence: list[JobEvidence]
    status: Literal["needs_review"] = "needs_review"


class KeywordCoverage(BaseModel):
    matched: int
    total: int
    percentage: float | None


class EditCandidate(BaseModel):
    candidate_id: int
    section: ResumeSection
    original_text: str
    suggested_replacement: str
    relevant_jd_keywords: list[str]
    supporting_resume_evidence: list[ResumeEvidence]
    reason: str


class UserSkillEvidence(BaseModel):
    keyword: str
    source: EvidenceSource
    text: ReplacementText


class Suggestion(BaseModel):
    suggestion_id: UUID
    section: ResumeSection
    original_text: str
    suggested_replacement: str
    relevant_jd_keywords: list[str]
    supporting_resume_evidence: list[ResumeEvidence]
    reason: str
    verification_status: Literal["verified_against_source", "user_attested"] = "verified_against_source"
    approval_status: Approval = "pending"
    manual_replacement: ReplacementText | None = None
    edit_revision: int = 0
    user_evidence: UserSkillEvidence | None = None

    @property
    def replacement_text(self) -> str:
        return self.manual_replacement or self.suggested_replacement


class SkillReview(BaseModel):
    status: ReviewStatus
    proposal: Suggestion | None = None


class OptimizationResult(BaseModel):
    status: Literal["completed", "no_candidates"]
    suggestions: list[Suggestion]
    discarded_suggestions: int = 0


class MatchResult(MatchRequest):
    match_id: UUID
    job_title: str | None
    matched_skills: list[KeywordMatch]
    partial_matches: list[KeywordMatch]
    missing_keywords: list[KeywordMatch]
    relevant_projects: list[RelevantResumeLine]
    relevant_experience: list[RelevantResumeLine]
    required_qualifications_needing_review: list[QualificationReview]
    keyword_coverage: KeywordCoverage
    optimization: OptimizationResult | None = None
    skill_reviews: dict[str, SkillReview] = Field(default_factory=dict)


class StoredMatch(BaseModel):
    report: MatchResult
    candidates: list[EditCandidate]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_status: Literal["accepted", "rejected"]


class ManualEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacement: ReplacementText | None


class SkillReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    status: ReviewStatus
    source: EvidenceSource | None = None
    evidence: ReplacementText | None = None


class CoverageBreakdown(BaseModel):
    required: KeywordCoverage
    preferred: KeywordCoverage
    overall: KeywordCoverage


class EditorView(BaseModel):
    job: JobAnalysis
    skills: dict[str, list[str]]
    original_sections: dict[str, list[str]]
    preview_sections: dict[str, list[str]]
    self_reported_additions: dict[str, list[str]]
    current_coverage: CoverageBreakdown
    proposed_coverage: CoverageBreakdown


class GeminiSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate_id: int = Field(ge=1)
    suggested_replacement: str = Field(min_length=1, max_length=700)


class GeminiOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    suggestions: list[GeminiSelection] = Field(max_length=8)
