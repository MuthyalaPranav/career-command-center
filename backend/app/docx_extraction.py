import re
from collections.abc import Iterator
from pathlib import Path
from zipfile import BadZipFile

from docx import Document
from docx.opc.exceptions import OpcError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import LxmlError

from .resume_extraction_models import DocxExtractionMetadata, DocxParagraph, ExtractionError, TableCell


def extract_docx(path: Path) -> tuple[str, DocxExtractionMetadata]:
    paragraphs: list[DocxParagraph] = []
    table_count = 0

    def visit(blocks: Iterator[Paragraph | Table], table_path: tuple[TableCell, ...] = ()) -> None:
        nonlocal table_count
        for block in blocks:
            if isinstance(block, Paragraph):
                style = block.style
                paragraphs.append(DocxParagraph(
                    paragraph_number=len(paragraphs) + 1,
                    text=block.text,
                    style=style.name if style is not None else None,
                    heading_level=heading_level(block),
                    table_path=table_path,
                ))
                continue

            table_count += 1
            table_number = table_count
            visited_cells = set()
            for row_number, row in enumerate(block.rows, start=1):
                for column, cell in enumerate(row.cells, start=row.grid_cols_before + 1):
                    if cell._tc in visited_cells:
                        continue
                    visited_cells.add(cell._tc)
                    location = TableCell(table_number, row_number, column)
                    visit(cell.iter_inner_content(), (*table_path, location))

    with path.open("rb") as source:
        try:
            document = Document(source)
            visit(document.iter_inner_content())
        except (BadZipFile, OpcError, LxmlError, KeyError, ValueError, TypeError, RecursionError) as error:
            raise ExtractionError("The stored DOCX could not be read for extraction.") from error

    raw_text = "\n".join(paragraph.text for paragraph in paragraphs)
    if not raw_text.strip():
        raise ExtractionError("The DOCX has no readable text in its paragraphs or tables.")
    return raw_text, DocxExtractionMetadata(paragraphs, table_count)


def heading_level(paragraph: Paragraph) -> int | None:
    rank = outline_rank(paragraph._p.pPr)
    if rank is not None:
        return rank if rank < 10 else None

    style = paragraph.style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        rank = outline_rank(style.element.pPr)
        if rank is not None:
            return rank if rank < 10 else None

        builtin_heading = re.fullmatch(r"Heading([1-9])", style.style_id)
        if builtin_heading:
            return int(builtin_heading[1])
        style = style.base_style
    return None


def outline_rank(properties) -> int | None:
    outline = properties.find(qn("w:outlineLvl")) if properties is not None else None
    value = outline.get(qn("w:val"), "") if outline is not None else ""
    return int(value) + 1 if re.fullmatch(r"[0-9]", value) else None
