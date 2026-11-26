import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, get_args
from uuid import UUID

from .job_models import JobAnalysis, JobEvidence
from .job_skills import CANONICAL_SKILLS, find_skills
from .match_models import (
    CoverageBreakdown, EditCandidate, KeywordCoverage, KeywordMatch, MatchResult, QualificationReview,
    RelevantResumeLine, ResumeEvidence, ResumeSection, ResumeSource, StoredMatch, Suggestion, UserSkillEvidence,
)

MATCH_SECTIONS = ("technical_skills", "work_experience", "projects")
# A narrower skill can support a related term without establishing the broader skill.
RELATED_SKILLS = {
    "AWS": {"AWS Lambda", "Amazon S3"},
    "CI/CD": {"GitHub Actions", "GitLab CI", "Jenkins"},
    "Generative AI": {"Large Language Models", "Prompt Engineering"},
    "Machine Learning": {"Deep Learning", "PyTorch", "TensorFlow", "scikit-learn"},
    "SQL": {"PostgreSQL", "MySQL", "SQLite", "SQL Server"},
}
NEGATED = re.compile(r"\b(?:without|no\s+(?!less\b|more\b)|none|never|not(?!\s+only\b)|(?:don|didn|haven|hasn)['’]t|lack(?:s|ed|ing)?)\b", re.I)
TENTATIVE = re.compile(
    r"\b(?:learning|studying|familiar(?:ity)?|exposure|introductory|beginner|basics|"
    r"planning to|plan(?:s|ned)? to|will (?:use|learn)|interested in)\b", re.I,
)
CLAUSE_BREAK = re.compile(r";|(?<=[.!?])\s+|\b(?:but|while|however)\b", re.I)
SHORTER_WORDING = re.compile(r"\b(?:utilized|utilised|utilizing|utilising|in order to)\b", re.I)
SHORTER = {"utilized": "used", "utilised": "used", "utilizing": "using", "utilising": "using", "in order to": "to"}
CONTACT_MARKER = re.compile(r"@|https?://|\bwww\.|(?<!\w)\+?\d(?:[ ().-]*\d){9,}(?!\w)", re.I)
# Matching aliases can represent a family or change a sentence's grammar. Only
# expand interchangeable spellings when generating prose.
SAFE_EXPANSIONS = {
    "js", "ts", "dotnet", "react.js", "reactjs", "react js", "nextjs", "nodejs", "node js",
    "expressjs", "postgres", "amazon web services", "gcp", "google cloud platform",
    "k8s", "ci / cd", "continuous integration and delivery", "genai", "gen ai", "restful api",
}
EVIDENCE_PER_KIND = 6
MAX_CANDIDATES = 24


@dataclass(frozen=True)
class ResumeLine:
    section: ResumeSection
    number: int
    text: str

    def evidence(self, matched_text: str) -> ResumeEvidence:
        return ResumeEvidence(section=self.section, line_number=self.number, text=self.text, matched_text=matched_text)


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def canonicalize(text: str) -> str:
    key = normalize(text)
    return CANONICAL_SKILLS.get(key, text.strip())


def index_resume(lines: list[ResumeLine]):
    index = defaultdict(list)
    for line in lines:
        for clause in CLAUSE_BREAK.split(line.text):
            if NEGATED.search(clause):
                continue
            for canonical, occurrence in find_skills(clause):
                kind = "contextual"
                if line.section in MATCH_SECTIONS and not TENTATIVE.search(clause):
                    kind = "direct"
                index[normalize(canonical)].append((kind, line.evidence(occurrence[0])))
    return index


def select_exact_evidence(
    direct: list[ResumeEvidence], jd_evidence: list[JobEvidence],
) -> tuple[Literal["exact", "alias"], list[ResumeEvidence], list[JobEvidence]]:
    jd_by_wording = {normalize(item.matched_text or item.value): item for item in reversed(jd_evidence)}
    for index, source in enumerate(direct):
        jd_source = jd_by_wording.get(normalize(source.matched_text))
        if jd_source is not None:
            return "exact", [source, *direct[:index], *direct[index + 1:]], [
                jd_source, *(item for item in jd_evidence if item is not jd_source),
            ]
    return "alias", direct, jd_evidence


def resume_lines(resume: ResumeSource) -> list[ResumeLine]:
    return [
        ResumeLine(section, number, line)
        for section in get_args(ResumeSection)
        for number, line in enumerate(resume.sections.get(section, []), 1)
        if line.strip()
    ]


def compare_resume(
    resume: ResumeSource, job: JobAnalysis, match_id: UUID, *, include_candidates: bool = True,
) -> StoredMatch:
    lines = resume_lines(resume)
    index = index_resume(lines)
    job_index = defaultdict(list)
    for item in job.evidence:
        if item.field == "skill" and item.classification != "negated":
            job_index[normalize(canonicalize(item.value))].append(item)

    by_kind = {"matched": [], "partial": [], "missing": []}
    relevant = {"projects": {}, "work_experience": {}}
    per_line = defaultdict(dict)
    seen = set()
    for priority, keywords in (
        ("required", job.required_skills), ("preferred", job.preferred_skills), ("mentioned", job.ats_keywords),
    ):
        for term in keywords:
            keyword = canonicalize(term)
            key = normalize(keyword)
            if not key or key in seen:
                continue
            seen.add(key)
            evidence = index[key]
            direct = [item for kind, item in evidence if kind == "direct"]
            contextual = [item for kind, item in evidence if kind == "contextual"]
            for related in sorted(RELATED_SKILLS.get(keyword, ())):
                contextual.extend(item for _, item in index[normalize(related)])

            # The extracted keyword can be shown as field-level evidence for older JD records.
            jd_evidence = job_index[key]
            if not jd_evidence:
                jd_evidence = [JobEvidence(
                    field="skill", value=keyword, snippet=term, line_number=0, classification=priority, matched_text=term,
                )]

            if direct:
                group = "matched"
                kind, supporting, jd_evidence = select_exact_evidence(direct, jd_evidence)
                if include_candidates:
                    for item in supporting:
                        per_line[item.section, item.line_number][keyword] = item
            elif contextual:
                group, kind, supporting = "partial", "contextual", contextual
            else:
                group, kind, supporting = "missing", "missing", []

            for item in supporting:
                if item.section in relevant:
                    row = relevant[item.section].setdefault(item.line_number, RelevantResumeLine(
                        line_number=item.line_number, text=item.text, keywords=[],
                    ))
                    if keyword not in row.keywords:
                        row.keywords.append(keyword)
            by_kind[group].append(KeywordMatch(
                keyword=keyword, priority=priority, match_type=kind,
                resume_evidence=supporting[:EVIDENCE_PER_KIND], jd_evidence=jd_evidence[:EVIDENCE_PER_KIND],
            ))

    confirmed = len(by_kind["matched"])
    total = len(seen)
    report = MatchResult(
        match_id=match_id, resume_id=resume.resume_id, job_id=job.job_id, job_title=job.job_title,
        matched_skills=by_kind["matched"], partial_matches=by_kind["partial"], missing_keywords=by_kind["missing"],
        relevant_projects=sorted(relevant["projects"].values(), key=lambda row: row.line_number),
        relevant_experience=sorted(relevant["work_experience"].values(), key=lambda row: row.line_number),
        required_qualifications_needing_review=qualification_review(job),
        keyword_coverage=KeywordCoverage(matched=confirmed, total=total, percentage=round(100 * confirmed / total, 1) if total else None),
    )
    if not include_candidates:
        return StoredMatch(report=report, candidates=[])

    # Summary mentions are contextual for coverage, but an explicit local alias can still be expanded safely.
    for line in lines:
        if line.section == "professional_summary" and not (NEGATED.search(line.text) or TENTATIVE.search(line.text)):
            for keyword, occurrence in find_skills(line.text):
                if normalize(keyword) in seen:
                    per_line[line.section, line.number][keyword] = line.evidence(occurrence[0])
    return StoredMatch(report=report, candidates=build_candidates(lines, per_line))


def qualification_review(job: JobAnalysis) -> list[QualificationReview]:
    review = []
    for field in ("experience_requirements", "education_requirements", "other_qualifications"):
        for requirement in getattr(job, field):
            evidence = [item for item in job.evidence if item.field == field and item.value == requirement]
            if not evidence or any(item.classification in {"required", "mentioned"} for item in evidence):
                review.append(QualificationReview(category=field, requirement=requirement, jd_evidence=evidence))
    return review


def coverage_breakdown(report: MatchResult) -> CoverageBreakdown:
    def coverage(priority: str) -> KeywordCoverage:
        matched = sum(item.priority == priority for item in report.matched_skills)
        total = matched + sum(item.priority == priority for item in (*report.partial_matches, *report.missing_keywords))
        return KeywordCoverage(matched=matched, total=total, percentage=round(100 * matched / total, 1) if total else None)

    return CoverageBreakdown(required=coverage("required"), preferred=coverage("preferred"), overall=report.keyword_coverage)


def grounded_replacement(section: ResumeSection, text: str, keywords: list[str]) -> str | None:
    if (not keywords or len(text) > 450 or CONTACT_MARKER.search(text)
            or NEGATED.search(text) or TENTATIVE.search(text)):
        return None

    replacement = text
    for keyword, occurrence in reversed(list(find_skills(text))):
        if keyword in keywords and normalize(occurrence[0]) in SAFE_EXPANSIONS:
            replacement = replacement[:occurrence.start()] + keyword + replacement[occurrence.end():]
    if section != "technical_skills":
        replacement = SHORTER_WORDING.sub(
            lambda match: SHORTER[match[0].casefold()].capitalize() if match[0][0].isupper()
            else SHORTER[match[0].casefold()], replacement,
        )
    return replacement if replacement != text and len(replacement) <= 700 else None


def manual_wording_supported(suggestion: Suggestion) -> bool:
    if suggestion.manual_replacement is None:
        return True

    def wording(text: str) -> str:
        grounded = grounded_replacement(suggestion.section, text, suggestion.relevant_jd_keywords) or text
        return " ".join(grounded.split()).removesuffix(".")

    text = suggestion.manual_replacement
    return text.isprintable() and wording(text) == wording(suggestion.suggested_replacement)


def attestation_supported(resume: ResumeSource, report: MatchResult, evidence: UserSkillEvidence) -> bool:
    text = evidence.text
    if (not text.isprintable() or len(re.findall(r"\w+", text)) < 3 or NEGATED.search(text)
            or TENTATIVE.search(text) or CONTACT_MARKER.search(text)):
        return False
    if not any(item.keyword == evidence.keyword for item in (*report.partial_matches, *report.missing_keywords)):
        return False
    skills = {skill for skill, _ in find_skills(text)}
    confirmed = {canonicalize(key) for key, matches in index_resume(resume_lines(resume)).items()
                 if any(kind == "direct" for kind, _ in matches)}
    return evidence.keyword in skills and skills <= confirmed | {evidence.keyword}


def attestation_section(evidence: UserSkillEvidence) -> ResumeSection:
    return "work_experience" if evidence.source == "work_experience" else "projects"


def build_candidates(lines: list[ResumeLine], supported: dict) -> list[EditCandidate]:
    candidates = []
    for line in lines:
        keywords = supported.get((line.section, line.number), {})
        replacement = grounded_replacement(line.section, line.text, list(keywords))
        if replacement is not None:
            candidates.append(EditCandidate(
                candidate_id=len(candidates) + 1, section=line.section, original_text=line.text,
                suggested_replacement=replacement, relevant_jd_keywords=list(keywords),
                supporting_resume_evidence=list(keywords.values()),
                reason="Use clearer wording or standard skill names for terms supported by this resume line.",
            ))
            if len(candidates) == MAX_CANDIDATES:
                break
    return candidates
