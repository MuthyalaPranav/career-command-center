import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

SECTION_HEADINGS = {
    "contact_information": ("contact information", "contact", "contact details", "contact info"),
    "professional_summary": (
        "professional summary", "summary", "profile", "professional profile", "career summary", "objective", "about me",
    ),
    "technical_skills": (
        "technical skills", "skills", "core skills", "technology stack", "tech stack", "competencies", "skills and expertise",
    ),
    "work_experience": (
        "work experience", "experience", "professional experience", "employment", "employment history", "work history",
    ),
    "projects": ("projects", "personal projects", "selected projects", "academic projects"),
    "education": ("education", "academic background", "academic qualifications"),
    "certifications": (
        "certifications", "professional certifications", "certificates", "licenses", "licenses and certifications",
        "certifications and licenses",
    ),
}
HEADING_KEYS = {heading: section for section, headings in SECTION_HEADINGS.items() for heading in headings}
UNCLASSIFIED_HEADINGS = {
    "activities", "additional information", "affiliations", "awards", "awards and honors", "community work", "interests",
    "languages", "publications", "references", "volunteering", "volunteer experience",
}
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE)
CONTACT_URL = re.compile(r"\b(?:https?://|www\.|(?:linkedin|github)\.com/)", re.IGNORECASE)
PHONE = re.compile(r"(?:phone\s*:\s*)?\+?[\d().\- ]+", re.IGNORECASE)


@dataclass(frozen=True)
class ResumeLine:
    text: str
    is_heading: bool = False


def parse_sections(lines: Iterable[ResumeLine]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {key: [] for key in (*SECTION_HEADINGS, "unclassified")}
    for _, section, text in classify_lines(lines):
        sections[section].append(text)
    return sections


def classify_lines(lines: Iterable[ResumeLine]) -> Iterator[tuple[int, str, str]]:
    current_section = None
    for index, line in enumerate(lines):
        text = line.text.strip()
        if not text:
            continue

        label, _, content = text.partition(":")
        normalized = unicodedata.normalize("NFKC", label).casefold().replace("&", " and ")
        heading = " ".join(normalized.split())
        recognized = HEADING_KEYS.get(heading)
        if recognized:
            current_section = recognized
            if content.strip():
                yield index, current_section, content.strip()
        elif line.is_heading or heading in UNCLASSIFIED_HEADINGS or is_unclassified_heading(text, label):
            if current_section is not None:
                current_section = "unclassified"
            yield index, "unclassified", text
        elif current_section is None and is_contact_line(text):
            yield index, "contact_information", text
        else:
            yield index, current_section or "unclassified", text


def is_contact_line(text: str) -> bool:
    if EMAIL.search(text) or CONTACT_URL.search(text):
        return True
    return PHONE.fullmatch(text) is not None and 7 <= sum(char.isdigit() for char in text) <= 15


def is_unclassified_heading(text: str, label: str) -> bool:
    return text.endswith(":") and 1 <= len(label.split()) <= 5 and all(
        char.isalpha() or char in " &/-" for char in label
    )
