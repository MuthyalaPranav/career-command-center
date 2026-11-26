import re
import unicodedata
from dataclasses import dataclass
from html import escape

import pymupdf

from .pdf_extraction import PDF_LOCK
from .resume_links import VISIBLE_LINK, link_destination

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.23; color: #172026; margin: 0; }
h1 { font-size: 20pt; margin: 0 0 4pt; line-height: 1.15; page-break-after: avoid; }
h2 { font-size: 12pt; margin: 10pt 0 4pt; line-height: 1.12; page-break-after: avoid; }
p { margin: 0 0 3pt; }
ul { margin: 0 0 3pt; padding-left: 16pt; }
li { margin-bottom: 2pt; }
a { color: #155552; text-decoration: underline; }
"""
BULLET = re.compile(r"^(?:[•●◦▪‣⁃]\s*|[-*]\s+)(.+)$")
LONG_TOKEN = re.compile(r"([!-~]{51,})")
LATIN_LIGATURE_START = re.compile(r"f(?=[fil])")
MAX_PAGES = 100


class PDFGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class ResumeContent:
    name: str
    contact: list[str]
    sections: list[tuple[str, list[str]]]
    links: dict[str, str]


def escaped_text(text: str) -> str:
    parts = LONG_TOKEN.split(text)
    for index, token in enumerate(parts):
        if index % 2:
            parts[index] = "&#8203;".join(escape(token[start:start + 24]) for start in range(0, len(token), 24))
        else:
            parts[index] = escape(token)
    # Separate glyph runs to keep common Latin ligatures searchable as letters.
    return LATIN_LIGATURE_START.sub("<span>f</span>", "".join(parts))


def linked_text(text: str, targets: set[str]) -> str:
    pieces = []
    offset = 0
    for match in VISIBLE_LINK.finditer(text):
        label = match[0].rstrip(".,;:!?)]}")
        destination = link_destination(label)
        if destination is None:
            continue
        pieces.append(escaped_text(text[offset:match.start()]))
        pieces.append(f'<a href="{escape(destination)}">{escaped_text(label)}</a>')
        targets.add(destination)
        offset = match.start() + len(label)
    pieces.append(escaped_text(text[offset:]))
    return "".join(pieces)


def render_html(content: ResumeContent) -> tuple[str, str, set[str]]:
    html = []
    expected = []
    targets: set[str] = set()

    def block(tag: str, text: str, prefix: str = "") -> None:
        html.append(f"<{tag}>{linked_text(text, targets)}</{tag}>")
        expected.append(prefix + text)

    if content.name:
        block("h1", content.name)
    for line in content.contact:
        if line.strip():
            block("p", line)
    for heading, lines in content.sections:
        block("h2", heading)
        in_list = False
        for line in lines:
            if not line.strip():
                continue
            bullet = BULLET.match(line.strip())
            if bullet and not in_list:
                html.append("<ul>")
            if not bullet and in_list:
                html.append("</ul>")
            in_list = bullet is not None
            if bullet:
                block("li", bullet[1], "• ")
            else:
                block("p", line)
        if in_list:
            html.append("</ul>")
    hidden_links = {url: label for url, label in content.links.items() if url not in targets}
    if hidden_links:
        block("h2", "Links")
        for url, label in hidden_links.items():
            visible_url = url.removeprefix("mailto:")
            prefix = f"{label} — " if label and label != visible_url else ""
            html.append(f'<p>{escaped_text(prefix)}<a href="{escape(url)}">{escaped_text(visible_url)}</a></p>')
            expected.append(prefix + visible_url)
            targets.add(url)
    return "".join(html), "\n".join(expected), targets


def comparable(text: str) -> str:
    # Line wrapping and paragraph layout may change whitespace.
    return "".join(unicodedata.normalize("NFC", text).split()).replace("\u200b", "")


def validate_pdf(data: bytes, expected: str, targets: set[str]) -> int:
    # The caller holds PDF_LOCK for rendering and validation.
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if not document.page_count or document.is_repaired or document.needs_pass:
            raise PDFGenerationError("The generated PDF could not be reopened.")
        text = []
        actual_targets = set()
        flags = pymupdf.TEXTFLAGS_TEXT & ~(pymupdf.TEXT_USE_CID_FOR_UNKNOWN_UNICODE | pymupdf.TEXT_PRESERVE_LIGATURES)
        for page in document:
            page_text = page.get_text(flags=flags)
            if not comparable(page_text):
                raise PDFGenerationError("The generated PDF contained a blank page.")
            text.append(page_text)
            actual_targets.update(link.get("uri") for link in page.get_links())
        if comparable("".join(text)) != comparable(expected):
            raise PDFGenerationError("Some text could not be preserved in the PDF. Check the source for unsupported characters.")
        if not targets <= actual_targets:
            raise PDFGenerationError("Some links could not be preserved in the generated PDF.")
        return document.page_count


def render_pdf(content: ResumeContent) -> tuple[bytes, int]:
    html, expected, targets = render_html(content)
    if not comparable(expected):
        raise PDFGenerationError("The resume extraction has no text to export.")

    def page_rect(number: int, _filled):
        if number >= MAX_PAGES:
            raise PDFGenerationError(f"The resume exceeds the {MAX_PAGES}-page export limit.")
        page = pymupdf.paper_rect("letter")
        return page, page + (48, 45, -48, -45), None

    try:
        with PDF_LOCK:
            story = pymupdf.Story(html=html, user_css=CSS)
            with story.write_with_links(page_rect) as document:
                document.set_metadata({"title": "Resume"})
                data = document.tobytes(garbage=3, deflate=True)
            return data, validate_pdf(data, expected, targets)
    except (RuntimeError, ValueError) as error:
        if isinstance(error, PDFGenerationError):
            raise
        raise PDFGenerationError("The resume could not be laid out as a PDF.") from error
