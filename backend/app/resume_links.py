import re
from io import BytesIO
from urllib.parse import urlsplit

import pymupdf
from docx import Document
from docx.opc.exceptions import OpcError
from docx.text.paragraph import Paragraph
from lxml.etree import LxmlError

from .pdf_extraction import PDF_LOCK
from .resume_sections import EMAIL

VISIBLE_LINK = re.compile(
    r"(?:https?://|www\.|\b(?:linkedin|github)\.com/)[^\s<>\u2022|]+"
    r"|[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE,
)


class SourceLinkError(ValueError):
    pass


def link_destination(value: str) -> str | None:
    if not value or any(char.isspace() or ord(char) < 32 or char in "<>\\\"'" for char in value):
        return None
    if EMAIL.fullmatch(value):
        return f"mailto:{value}"
    if value.lower().startswith("mailto:"):
        return value if EMAIL.fullmatch(value[7:]) else None
    if value.lower().startswith(("www.", "linkedin.com/", "github.com/")):
        value = f"https://{value}"
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in {"http", "https"} and parsed.hostname and "." in parsed.hostname
                and not parsed.username and not parsed.password):
            return value
    except ValueError:
        return None
    return None


def docx_links(blocks):
    for block in blocks:
        if isinstance(block, Paragraph):
            for link in block.hyperlinks:
                yield link.url, link.text
        else:
            visited = set()
            for row in block.rows:
                for cell in row.cells:
                    if cell._tc not in visited:
                        visited.add(cell._tc)
                        yield from docx_links(cell.iter_inner_content())


def pdf_links(source: bytes):
    with PDF_LOCK, pymupdf.open(stream=source, filetype="pdf") as document:
        for page in document:
            for link in page.get_links():
                if link["kind"] == pymupdf.LINK_URI:
                    box = link["from"] * page.derotation_matrix
                    label = page.get_textbox(box + (-1, -2, 1, 2))
                    yield link["uri"], label


def original_links(source: bytes, extension: str) -> dict[str, str]:
    try:
        pairs = pdf_links(source) if extension == ".pdf" else docx_links(
            Document(BytesIO(source)).iter_inner_content(),
        )
        links = {}
        for uri, label in pairs:
            destination = link_destination(uri)
            if destination is not None:
                links.setdefault(destination, " ".join(label.split()))
        return links
    except (OpcError, LxmlError, RuntimeError, ValueError, KeyError, RecursionError) as error:
        raise SourceLinkError("The original resume could not be read to preserve its links.") from error
