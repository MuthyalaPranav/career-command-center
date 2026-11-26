from collections import defaultdict

from . import match_storage
from .match_models import EditorView, StoredMatch, Suggestion
from .resume_generation import (
    append_attestations, approved_attestations, approved_suggestions, edited_sections, resolve_approved,
    resolve_attestations,
)
from .resume_matching import canonicalize, compare_resume, coverage_breakdown, index_resume, resume_lines


def suggestions_for(record: StoredMatch):
    if record.report.optimization:
        yield from record.report.optimization.suggestions
    for review in record.report.skill_reviews.values():
        if review.proposal is not None:
            yield review.proposal


def approval_is_supported(record: StoredMatch, suggestion: Suggestion) -> bool:
    resume = match_storage.load_resume(record.report.resume_id)
    job = match_storage.load_job(record.report.job_id)
    if suggestion.verification_status == "user_attested":
        return bool(resolve_attestations(resume, record, [suggestion], job))
    return bool(resolve_approved(resume, record, [suggestion], job=job))


def editor_view(record: StoredMatch) -> EditorView:
    resume = match_storage.load_resume(record.report.resume_id)
    job = match_storage.load_job(record.report.job_id)
    current = compare_resume(resume, job, record.report.match_id, include_candidates=False).report
    changes = resolve_approved(resume, record, approved_suggestions(record), job=job)
    proposed_sections = edited_sections(resume, changes)
    proposed = compare_resume(
        resume.model_copy(update={"sections": proposed_sections}), job, record.report.match_id, include_candidates=False,
    ).report
    additions = resolve_attestations(resume, record, approved_attestations(record), job)
    append_attestations(proposed_sections, additions)
    self_reported = defaultdict(list)
    for item in additions:
        self_reported[item.section].append(item.replacement_text)
    skills = defaultdict(set)
    for keyword, evidence in index_resume(resume_lines(resume)).items():
        for _, item in evidence:
            skills[item.section].add(canonicalize(keyword))
    return EditorView(
        job=job, skills={section: sorted(values, key=str.casefold) for section, values in skills.items()},
        original_sections=resume.sections, preview_sections=proposed_sections,
        self_reported_additions=self_reported,
        current_coverage=coverage_breakdown(current), proposed_coverage=coverage_breakdown(proposed),
    )
