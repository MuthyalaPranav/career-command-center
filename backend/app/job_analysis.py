import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import cast

from .job_models import Classification, EvidenceField, JobAnalysisDetails, JobEvidence
from .job_skills import find_skills

SECTIONS = {
    "required": ("required", "requirements", "required skills", "required qualifications", "minimum qualifications",
                 "must have", "must haves", "mandatory skills", "what you bring", "what you'll bring"),
    "preferred": ("preferred", "preferred skills", "preferred qualifications", "desired skills", "desired qualifications",
                  "nice to have", "nice to haves", "bonus", "bonus skills", "good to have"),
    "experience_requirements": ("experience", "experience requirements", "work experience", "professional experience"),
    "education_requirements": ("education", "educational requirements", "education requirements", "academic qualifications"),
    "responsibilities": ("responsibilities", "key responsibilities", "job responsibilities", "duties", "what you'll do", "what you will do"),
    "other_qualifications": ("other qualifications", "additional qualifications", "soft skills", "certifications", "licenses"),
    "mentioned": ("skills", "technical skills", "tools", "tools and technologies", "technologies", "tech stack", "tooling",
                  "qualifications", "about us", "about the role", "benefits", "location", "compensation"),
}
SECTION_KEYS = {heading: section for section, headings in SECTIONS.items() for heading in headings}
TITLE_LABEL = re.compile(r"(?:job title|position(?: title)?|role(?: title)?|title)\s*[:：]\s*(.+)", re.I)
TITLE_HEADING = re.compile(r"(?:job title|position(?: title)?|role title)\s*[:：]?", re.I)
TITLE_ROLE = re.compile(
    r"\b(?:developer|engineer|architect|analyst|scientist|designer|manager|specialist|administrator|"
    r"consultant|technician|coordinator|director|intern|lead)\b(?:\s+(?:I{1,3}|IV|[1-4]))?"
    r"(?:\s*(?:\([^()]+\)|[-–—]\s*(?:remote|hybrid|on[ -]site)))?$", re.I,
)
TITLE_SENTENCE = re.compile(r"\b(?:we|you|our|join|hiring|experience|requires?|have|will|for|every)\b", re.I)
HIRING = re.compile(r"(?:we(?: are|'re|’re) )?(?:hiring|looking for|seeking) (?:an? )?(.+)", re.I)
LIST_MARKER = re.compile(r"^(?:\s*[-*•▪◦–—]\s+|\s*\d+[.)]\s+|\s*#{1,6}\s+)")
CLAUSE_BREAK = re.compile(r"\s*;\s*|(?<=[.!?])\s+")
CONTEXT_BREAK = re.compile(r"\b(?:but|while|whereas|however|although|except)\b|,\s*(?:plus|with)\b", re.I)
PARENTHETICAL_GAP = re.compile(r"\s*(?:(?:experience|knowledge)\s+)?\(\s*", re.I)
EXPERIENCE = re.compile(
    r"\b(?:\d+(?:\.\d+)?(?:\s*[-–]\s*\d+)?\s*\+?\s*(?:years?|yrs?|months?)"
    r"|(?:professional|hands-on|practical|relevant|prior|demonstrated|industry)\s+experience"
    r"|experience\s+(?:with|in|using|building|developing|working))\b", re.I,
)
EDUCATION = re.compile(
    r"\b(?:(?:bachelor|master|associate)(?:['’]s)?\s+(?:degree|of|in)|(?:bachelor|master|associate)['’]s\b"
    r"|(?:university|college|graduate|undergraduate|doctoral|advanced|relevant)\s+degree"
    r"|degree\s+in|high school diploma|Ph\.?D\.?|B\.?Sc\.?|M\.?Sc\.?|MBA|B\.S\.|M\.S\.)\b"
    r"|\bdegree\b(?=\s+(?:is\s+)?(?:required|preferred|optional|not\s+required))", re.I,
)
RESPONSIBILITY = re.compile(r"^(?:you(?: will|'ll|’ll)\b|responsible for\b|responsibilities include\b)", re.I)
OTHER_QUALIFICATION = re.compile(
    r"\b(?:certifications?|work authorization|authorized to work|security clearance|ability to|"
    r"communication skills|teamwork|attention to detail|background check|valid (?:driver['’]s )?license)\b", re.I,
)

COPULA = r"(?:is|are|was|were|would be|will be)"
NEED = r"(?:required|mandatory|necessary|needed|essential|a requirement|a prerequisite|a must)"
WANT = r"(?:preferred|desirable|optional|a plus|a bonus|nice[- ]to[- ]have|good[- ]to[- ]have|an advantage)"
CUES = re.compile(
    rf"(?P<preferred_suffix>\b(?:{COPULA}\s+)?not\s+(?:strictly\s+)?{NEED}\s*,?\s+but\s+(?:{COPULA}\s+)?{WANT}\b)"
    rf"|(?P<preferred_suffix_negation>\b(?:{COPULA}\s+)?{WANT}\s*,?\s+but\s+(?:{COPULA}\s+)?not\s+{NEED}\b)"
    rf"|(?P<negated_suffix>\b(?:(?:{COPULA}\s+)?not\s+(?:strictly\s+)?(?:{NEED}|{WANT})"
    rf"|(?:isn|aren|wasn|weren)['’]t\s+{NEED})\b)"
    r"|(?P<negated_prefix>\b(?:no(?!\s+(?:later|less|more|fewer)\b)|without|(?:do not|don['’]t)\s+(?:need|require))\b)"
    r"|(?P<required_prefix>\b(?:must\s+(?:have|know|possess|demonstrate|use|be proficient in|be familiar with)"
    r"|(?:we\s+)?required(?=\s+(?:proficiency|knowledge|experience)\b)|(?:we\s+)?requires?|minimum(?:\s+of)?|at least)\b)"
    r"|(?P<preferred_prefix>\b(?:we\s+)?prefers?\b)"
    rf"|(?P<preferred_suffix_aux>\b{COPULA}\s+{WANT}\b)"
    rf"|(?P<required_suffix>\b{COPULA}\s+{NEED}\b)"
    rf"|(?P<preferred_bare>\b{WANT}\b)"
    rf"|(?P<required_bare>\b{NEED}\b)", re.I,
)
SKILL_PRIORITY = {"negated": -1, "mentioned": 0, "preferred": 1, "required": 2}


@dataclass(frozen=True)
class Cue:
    classification: Classification
    direction: str
    start: int
    end: int
    parenthetical: bool


class ClauseContext:
    def __init__(self, text: str, default: Classification):
        self.text = text
        self.default = default
        self.cues = []
        for match in CUES.finditer(text):
            classification, _, direction = (match.lastgroup or "mentioned_bare").partition("_")
            if direction == "bare":
                preceding_end = self.cues[-1].end if self.cues else 0
                prior = text[preceding_end:match.start()].strip(" ,:-–—\t").casefold()
                starts_group = prior in {"", "and", "but", "while", "whereas", "however", "although", "except"}
                direction = "prefix" if starts_group or text[match.end():].lstrip().startswith(":") else "suffix"
            self.cues.append(Cue(
                cast(Classification, classification), direction, match.start(), match.end(),
                text[:match.start()].rstrip().endswith("("),
            ))
        self.starts = [cue.start for cue in self.cues]
        self.ends = [cue.end for cue in self.cues]

    def classify(self, start: int, end: int) -> Classification:
        candidates = []
        previous = bisect_right(self.ends, start) - 1
        following = bisect_left(self.starts, end)
        if previous >= 0:
            cue = self.cues[previous]
            gap = self.text[cue.end:start]
            if cue.direction == "prefix" and not CONTEXT_BREAK.search(gap):
                candidates.append((cue.classification, len(gap)))
        if following < len(self.cues):
            cue = self.cues[following]
            gap = self.text[end:cue.start]
            if (
                cue.direction.startswith("suffix")
                and not CONTEXT_BREAK.search(gap)
                and (not cue.parenthetical or PARENTHETICAL_GAP.fullmatch(gap))
            ):
                candidates.append((cue.classification, len(gap)))
        if any(classification == "negated" for classification, _ in candidates):
            return "negated"
        return min(candidates, key=lambda candidate: candidate[1])[0] if candidates else self.default


def analyze_job_description(job_description: str) -> JobAnalysisDetails:
    source_lines = job_description.splitlines()
    details = JobAnalysisDetails()
    title = find_title(source_lines)
    if title:
        line_number, details.job_title = title
        details.evidence.append(JobEvidence(
            field="job_title", value=details.job_title, snippet=source_lines[line_number - 1].strip(),
            line_number=line_number, classification="mentioned",
        ))

    section = "mentioned"
    skills: dict[str, Classification] = {}
    for line_number, source in enumerate(source_lines, start=1):
        snippet = source.strip()
        text = LIST_MARKER.sub("", snippet).strip().strip("*").strip()
        if not text or TITLE_HEADING.fullmatch(text):
            continue

        label, _, value = text.replace("：", ":", 1).partition(":")
        heading = " ".join(label.strip(" *").casefold().replace("’", "'").replace("-", " ").split())
        if not (title and title[0] == line_number) and heading in SECTION_KEYS:
            section = SECTION_KEYS[heading]
            text = value.strip()
        default = cast(Classification, section) if section in {"required", "preferred"} else "mentioned"
        for clause in filter(str.strip, CLAUSE_BREAK.split(text)):
            context = ClauseContext(clause, default)
            clause_offset = max(0, snippet.find(clause))
            skill_found = False
            for canonical, match in find_skills(clause):
                classification = context.classify(*match.span())
                skill_found = True
                if classification != "negated" and SKILL_PRIORITY[classification] > SKILL_PRIORITY[skills.get(canonical, "negated")]:
                    skills[canonical] = classification
                details.evidence.append(JobEvidence(
                    field="skill", value=canonical,
                    snippet=evidence_snippet(snippet, clause_offset + match.start(), clause_offset + match.end()),
                    line_number=line_number,
                    classification=classification, matched_text=match[0],
                ))
            if not (title and title[0] == line_number):
                for field, anchor in qualification_matches(clause, section, skill_found):
                    classification = context.classify(*anchor)
                    value = clause.strip()
                    entries = getattr(details, field)
                    if classification != "negated" and value not in entries:
                        entries.append(value)
                    details.evidence.append(JobEvidence(
                        field=field, value=value,
                        snippet=evidence_snippet(snippet, clause_offset + anchor[0], clause_offset + anchor[1]),
                        line_number=line_number, classification=classification,
                    ))

    details.required_skills = [skill for skill, classification in skills.items() if classification == "required"]
    details.preferred_skills = [skill for skill, classification in skills.items() if classification == "preferred"]
    details.tools_and_technologies = [skill for skill, classification in skills.items() if classification == "mentioned"]
    details.ats_keywords = list(skills)
    details.evidence.sort(key=lambda item: item.line_number)
    return details


def evidence_snippet(line: str, start: int, end: int) -> str:
    if len(line) <= 240:
        return line
    return line[max(0, start - 100):end + 100].strip()


def qualification_matches(clause: str, section: str, skill_found: bool) -> list[tuple[EvidenceField, tuple[int, int]]]:
    matches: list[tuple[EvidenceField, tuple[int, int]]] = []
    for field, pattern in (
        ("responsibilities", RESPONSIBILITY),
        ("experience_requirements", EXPERIENCE),
        ("education_requirements", EDUCATION),
        ("other_qualifications", OTHER_QUALIFICATION),
    ):
        match = pattern.search(clause)
        if match or section == field:
            matches.append((field, match.span() if match else clause_anchor(clause)))
    if not matches and section in {"required", "preferred"} and not skill_found and any(char.isalpha() for char in clause):
        matches.append(("other_qualifications", clause_anchor(clause)))
    return matches


def clause_anchor(clause: str) -> tuple[int, int]:
    """Use the start of unlabeled content so an explicit trailing cue can classify it."""
    start = len(clause) - len(clause.lstrip())
    return start, start


def find_title(source_lines: list[str]) -> tuple[int, str] | None:
    nonempty = [
        (number, LIST_MARKER.sub("", line.strip()).strip("*").strip())
        for number, line in enumerate(source_lines, 1) if line.strip()
    ]
    for index, (number, line) in enumerate(nonempty):
        label = TITLE_LABEL.fullmatch(line)
        if label:
            return number, label[1].strip()
        if TITLE_HEADING.fullmatch(line) and index + 1 < len(nonempty):
            next_number, next_line = nonempty[index + 1]
            if ":" not in next_line and len(next_line.split()) <= 12 and TITLE_ROLE.search(next_line):
                return next_number, next_line
    if nonempty:
        number, line = nonempty[0]
        hiring = HIRING.fullmatch(line)
        candidate = (hiring[1] if hiring else line).rstrip(".!").strip()
        if 1 <= len(candidate.split()) <= 12 and TITLE_ROLE.search(candidate) and not TITLE_SENTENCE.search(candidate):
            return number, candidate
    return None
