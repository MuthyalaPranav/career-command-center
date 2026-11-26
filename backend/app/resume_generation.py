import re
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from . import match_storage
from .generation_models import GenerationRequest, GenerationSource, LayoutValidation, VersionContent, VersionSnapshot
from .job_models import JobAnalysis
from .match_models import EditCandidate, ResumeSection, ResumeSource, StoredMatch, Suggestion
from .resume_links import original_links
from .resume_matching import attestation_section, attestation_supported, compare_resume, manual_wording_supported
from .resume_pdf_template import BULLET, ResumeContent, render_pdf
from .resume_sections import HEADING_KEYS, UNCLASSIFIED_HEADINGS, is_contact_line
from .resume_storage import find_resume

SECTION_ORDER = {
    "professional_summary": "Professional Summary",
    "technical_skills": "Technical Skills",
    "work_experience": "Work Experience",
    "projects": "Projects",
    "education": "Education",
    "certifications": "Certifications",
}
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE = rf"(?:{MONTH}\.?\s+)?(?:19|20)\d{{2}}"
DATE_LINE = re.compile(rf"(?P<start>{DATE})(?:\s*(?:[-–—]|\bto\b)\s*(?P<end>{DATE}|Present|Current))?", re.I)


class MatchMismatch(ValueError):
    pass


class SourceChanged(ValueError):
    pass


@dataclass(frozen=True)
class PreparedPDF:
    data: bytes
    page_count: int
    approved: int
    applied: int
    snapshot: VersionSnapshot
    validation: LayoutValidation | None = None


def edit_key(item: EditCandidate | Suggestion) -> tuple:
    return (
        item.section, item.original_text, item.suggested_replacement, frozenset(item.relevant_jd_keywords),
        frozenset((e.section, e.line_number, e.text, e.matched_text) for e in item.supporting_resume_evidence),
    )


def approved_suggestions(match: StoredMatch) -> list[Suggestion]:
    return [
        item for item in match.report.optimization.suggestions if item.approval_status == "accepted"
    ] if match.report.optimization else []


def approved_attestations(match: StoredMatch) -> list[Suggestion]:
    return [
        review.proposal for review in match.report.skill_reviews.values()
        if review.status == "already_know" and review.proposal and review.proposal.approval_status == "accepted"
    ]


def resolve_attestations(
    resume: ResumeSource, match: StoredMatch, suggestions: list[Suggestion], job: JobAnalysis | None,
) -> list[Suggestion]:
    if not suggestions or job is None:
        return []
    report = compare_resume(resume, job, match.report.match_id, include_candidates=False).report
    return [
        item for item in suggestions
        if item.verification_status == "user_attested" and item.user_evidence is not None
        and item.manual_replacement is None and item.suggested_replacement == item.user_evidence.text
        and item.section == attestation_section(item.user_evidence)
        and attestation_supported(resume, report, item.user_evidence)
    ]


def resolve_approved(
    resume: ResumeSource, match: StoredMatch, approved: list[Suggestion], *, job: JobAnalysis | None = None,
) -> dict[tuple[ResumeSection, int], Suggestion]:
    if not approved:
        return {}

    stored_keys = {edit_key(item) for item in match.candidates}
    job = job or match_storage.load_job(match.report.job_id)
    current = compare_resume(resume, job, match.report.match_id)
    current_candidates = {edit_key(item): item for item in current.candidates}
    resolved = {}
    for suggestion in approved:
        key = edit_key(suggestion)
        candidate = current_candidates.get(key)
        if (candidate is None or key not in stored_keys or suggestion.verification_status != "verified_against_source"
                or suggestion.user_evidence is not None or not manual_wording_supported(suggestion)):
            continue
        line_index = candidate.supporting_resume_evidence[0].line_number - 1
        position = candidate.section, line_index
        resolved.setdefault(position, suggestion)
    return resolved


def edited_sections(resume: ResumeSource, resolved: dict[tuple[ResumeSection, int], Suggestion]) -> dict[str, list[str]]:
    sections = {key: lines.copy() for key, lines in resume.sections.items()}
    for (section, index), suggestion in resolved.items():
        sections[section][index] = suggestion.replacement_text
    return sections


def append_attestations(sections: dict[str, list[str]], suggestions: list[Suggestion]) -> None:
    for item in suggestions:
        lines = sections.setdefault(item.section, [])
        if item.replacement_text not in lines:
            lines.append(item.replacement_text)


def snapshot_for(
    content: VersionContent, match: StoredMatch, job: JobAnalysis | None, applied: list[Suggestion],
) -> VersionSnapshot:
    return VersionSnapshot(
        content=content, job_id=match.report.job_id, job_title=match.report.job_title,
        keywords=job.ats_keywords if job else None, applied_suggestions=applied,
    )


def load_generation_job(match: StoredMatch, approved: list[Suggestion]) -> JobAnalysis | None:
    try:
        return match_storage.load_job(match.report.job_id)
    except (match_storage.RecordNotFound, match_storage.InvalidRecord):
        # Older comparisons can still export original wording if their JD is missing.
        if approved:
            raise
        return None


def restore_bullets(source: GenerationSource, sections: dict[str, list[str]]) -> None:
    if source.extraction_metadata is None:
        return
    bullet_lines = {
        line.strip() for paragraph in source.extraction_metadata.paragraphs
        if re.fullmatch(r"List Bullet(?: [2-9])?", paragraph.style or "", flags=re.I)
        for line in paragraph.text.splitlines()
    }
    if not bullet_lines:
        return
    counts = Counter(line for lines in source.sections.values() for line in lines)
    for key, lines in source.sections.items():
        for index, original in enumerate(lines):
            if counts[original] == 1 and original in bullet_lines and not BULLET.match(original):
                sections[key][index] = f"• {sections[key][index]}"


def format_date_line(line: str) -> str:
    match = DATE_LINE.fullmatch(line.strip())
    if not match:
        return line

    def format_part(value: str) -> str:
        parts = value.split()
        return f"{parts[0][:3].title()} {parts[1]}" if len(parts) == 2 else value.title()

    start, end = match.group("start", "end")
    return " – ".join(format_part(value) for value in (start, end) if value)


def prepare_content(
    raw_text: str, sections: dict[str, list[str]], links: dict[str, str],
) -> ResumeContent:
    # The extractor keeps a plain name or address before the first heading in
    # Unclassified. Recover the leading lines using the raw text's order.
    unclassified = deque(sections.get("unclassified", []))
    contact = sections.get("contact_information", [])
    remaining_contact = Counter(contact)
    header = []
    for line in (text.strip() for text in raw_text.splitlines() if text.strip()):
        label = unicodedata.normalize("NFKC", line.partition(":")[0]).casefold().replace("&", " and ")
        heading = " ".join(label.split())
        if heading in HEADING_KEYS or heading in UNCLASSIFIED_HEADINGS:
            break
        if remaining_contact[line]:
            header.append(line)
            remaining_contact[line] -= 1
        elif unclassified and line == unclassified[0]:
            header.append(unclassified.popleft())
        else:
            break

    # Without a contact or section anchor, keep a wholly unclassified source
    # in its normal section.
    if not contact and not any(sections.get(key) for key in SECTION_ORDER):
        unclassified = deque(sections.get("unclassified", []))
        header = []
    for line in contact:
        if remaining_contact[line]:
            header.append(line)
            remaining_contact[line] -= 1

    name = ""
    if header and not is_contact_line(header[0]) and len(header[0].split()) <= 6:
        if all(char.isalpha() or unicodedata.combining(char) or char in " .-'’" for char in header[0]):
            name = header.pop(0)
    ordered = [
        (label, [format_date_line(line) for line in sections[key]])
        for key, label in SECTION_ORDER.items() if any(sections.get(key, []))
    ]
    ordered.extend(
        (key.replace("_", " ").title(), lines)
        for key, lines in sections.items()
        if key not in SECTION_ORDER and key not in {"contact_information", "unclassified"} and any(lines)
    )
    if any(unclassified):
        ordered.append(("Additional Information", list(unclassified)))
    return ResumeContent(name, header, ordered, links)


def load_comparison(resume_id: UUID, match_id: UUID) -> StoredMatch:
    match = match_storage.load_match(match_id)
    if match.report.resume_id != resume_id:
        raise MatchMismatch("The selected comparison belongs to a different resume.")
    return match


def prepare_pdf(request: GenerationRequest, upload_directory: Path) -> PreparedPDF:
    match = load_comparison(request.resume_id, request.match_id)
    resume = match_storage.read_record(
        match_storage.EXTRACTION_DIRECTORY, request.resume_id, GenerationSource, "resume_id", "Resume extraction",
    )
    try:
        original_path = find_resume(upload_directory, request.resume_id)
    except FileNotFoundError as error:
        raise match_storage.RecordNotFound("Original uploaded resume") from error
    original = original_path.read_bytes()
    approved = approved_suggestions(match)
    attested = approved_attestations(match)
    job = load_generation_job(match, [*approved, *attested])
    resolved = resolve_approved(resume, match, approved, job=job)
    additions = resolve_attestations(resume, match, attested, job)
    sections = edited_sections(resume, resolved)
    restore_bullets(resume, sections)
    append_attestations(sections, additions)
    links = original_links(original, original_path.suffix)
    content = prepare_content(resume.raw_text, sections, links)
    pdf, pages = render_pdf(content)
    if original_path.read_bytes() != original:
        raise SourceChanged("The original resume changed during generation. Extract it again before retrying.")
    section_keys = {label: key for key, label in SECTION_ORDER.items()}
    snapshot = snapshot_for(VersionContent(
        name=content.name, contact=content.contact, links=content.links,
        sections={section_keys.get(label, label): lines for label, lines in content.sections},
    ), match, job, [*resolved.values(), *additions])
    return PreparedPDF(pdf, pages, len(approved) + len(attested), len(resolved) + len(additions), snapshot)
