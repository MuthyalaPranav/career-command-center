import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from . import generation_storage, resume_history
from .job_models import JobAnalysis
from .match_models import KeywordCoverage, KeywordMatch, QualificationReview, RelevantResumeLine, ResumeSource
from .resume_matching import NEGATED, TENTATIVE, compare_resume, normalize
from .resume_validation import PDF_TYPE
from .settings import REUSE_THRESHOLD


class QualificationExcerpt(BaseModel):
    requirement: str
    section: str
    text: str


class ReuseCandidate(BaseModel):
    generation_id: UUID
    name: str | None
    job_title: str | None
    keyword_coverage: KeywordCoverage
    required_coverage: KeywordCoverage
    preferred_coverage: KeywordCoverage
    matched_keywords: list[KeywordMatch]
    partial_keywords: list[KeywordMatch]
    missing_keywords: list[KeywordMatch]
    relevant_projects: list[RelevantResumeLine]
    relevant_experience: list[RelevantResumeLine]
    qualification_evidence: list[QualificationExcerpt]
    qualifications_to_review: list[QualificationReview]
    suitable: bool
    explanation: str


class ReuseRecommendations(BaseModel):
    job_id: UUID
    active_master_id: UUID | None
    recommended_generation_id: UUID | None
    recommendation: Literal["reuse", "create_from_master", "choose_master"]
    threshold: int
    candidates: list[ReuseCandidate]


def qualification_evidence(
    requirements: list[QualificationReview], sections: dict[str, list[str]],
) -> list[QualificationExcerpt]:
    fields = {
        "experience_requirements": ("work_experience", "projects", "professional_summary"),
        "education_requirements": ("education",),
        "other_qualifications": ("certifications", "education", "work_experience", "projects"),
    }
    # A literal phrase gives the reviewer a useful excerpt without inferring years, degrees or equivalence.
    excerpts = []
    for item in requirements:
        phrase = normalize(item.requirement).strip(".;:")
        if len(phrase.split()) < 2:
            continue
        pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)")
        for section in fields[item.category]:
            line = next((
                row for row in sections.get(section, ())
                if pattern.search(normalize(row)) and not (NEGATED.search(row) or TENTATIVE.search(row))
            ), None)
            if line:
                excerpts.append(QualificationExcerpt(requirement=item.requirement, section=section, text=line))
                break
    return excerpts


def rank_versions(job: JobAnalysis, master_id: UUID | None) -> ReuseRecommendations:
    ranked = []
    threshold = int(REUSE_THRESHOLD)
    for stored, directory in resume_history.generation_records():
        path = generation_storage.generation_pdf(directory)
        if path is None:
            continue
        snapshot = stored.snapshot or resume_history.legacy_snapshot(stored, directory, path)
        if snapshot.content is None:
            continue
        source = ResumeSource(resume_id=stored.resume_id, file_type=PDF_TYPE, sections=snapshot.content.sections)
        report = compare_resume(source, job, stored.generation_id, include_candidates=False).report
        matched, partial, missing = report.matched_skills, report.partial_matches, report.missing_keywords

        def coverage(priority: str) -> KeywordCoverage:
            count = sum(item.priority == priority for item in matched)
            total = count + sum(item.priority == priority for item in [*partial, *missing])
            return KeywordCoverage(matched=count, total=total, percentage=round(100 * count / total, 1) if total else None)

        required, preferred = coverage("required"), coverage("preferred")
        overall = report.keyword_coverage
        suitable = (
            overall.total > 0 and overall.matched * 100 >= overall.total * threshold
            and (not required.total or required.matched * 100 >= required.total * threshold)
        )
        qualifications = qualification_evidence(report.required_qualifications_needing_review, source.sections)
        explanation = (
            f"Confirmed {required.matched} of {required.total} required skills and "
            f"{preferred.matched} of {preferred.total} preferred skills. "
            f"Found {len(report.relevant_projects)} relevant project and "
            f"{len(report.relevant_experience)} work experience excerpts."
        )
        if overall.total == 0:
            explanation = "The JD contains no recognized technical keywords. Review the requirements manually."
        elif report.required_qualifications_needing_review:
            explanation += " Review the qualifications against the original requirements."
        prefs = resume_history.read_preferences(directory)
        candidate = ReuseCandidate(
            generation_id=stored.generation_id, name=prefs.name, job_title=snapshot.job_title,
            keyword_coverage=overall, required_coverage=required, preferred_coverage=preferred,
            matched_keywords=matched, partial_keywords=partial, missing_keywords=missing,
            relevant_projects=report.relevant_projects, relevant_experience=report.relevant_experience,
            qualification_evidence=qualifications, qualifications_to_review=report.required_qualifications_needing_review,
            suitable=suitable, explanation=explanation,
        )
        ranked.append((
            suitable, required.percentage or 0, preferred.percentage or 0, overall.percentage or 0,
            len(qualifications), len(report.relevant_projects) + len(report.relevant_experience),
            snapshot.created_at or datetime.min.replace(tzinfo=timezone.utc), str(stored.generation_id), candidate,
        ))

    candidates = [row[-1] for row in sorted(ranked, key=lambda row: row[:-1], reverse=True)[:3]]
    recommended = next((item.generation_id for item in candidates if item.suitable), None)
    return ReuseRecommendations(
        job_id=job.job_id, active_master_id=master_id, recommended_generation_id=recommended, threshold=threshold,
        recommendation="reuse" if recommended else "create_from_master" if master_id else "choose_master",
        candidates=candidates,
    )
