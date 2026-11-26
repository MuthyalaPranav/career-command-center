from pathlib import Path
from uuid import UUID

from .docx_extraction import extract_docx
from .pdf_extraction import extract_pdf
from .resume_extraction_models import ResumeExtraction
from .resume_sections import ResumeLine, parse_sections
from .resume_storage import find_resume
from .resume_validation import DOCX_TYPE, PDF_TYPE


def extract_resume(directory: Path, resume_id: UUID) -> ResumeExtraction:
    path = find_resume(directory, resume_id)
    if path.suffix == ".pdf":
        raw_text, metadata, layout = extract_pdf(path)
        sections = parse_sections(ResumeLine(line) for line in raw_text.splitlines())
        return ResumeExtraction(resume_id, PDF_TYPE, raw_text, sections, metadata, layout)

    raw_text, metadata = extract_docx(path)
    lines = (
        ResumeLine(line, is_heading=paragraph.heading_level is not None and index == 0)
        for paragraph in metadata.paragraphs
        for index, line in enumerate(paragraph.text.strip().splitlines())
    )
    sections = parse_sections(lines)
    return ResumeExtraction(resume_id, DOCX_TYPE, raw_text, sections, metadata)
