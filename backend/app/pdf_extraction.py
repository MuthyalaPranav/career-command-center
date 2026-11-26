from pathlib import Path
from threading import Lock

import pymupdf

from .resume_extraction_models import (
    ExtractionError, PdfBlock, PdfExtractionMetadata, PdfLayout, PdfLine, PdfPage, PdfSpan,
)

# PyMuPDF calls must not run in parallel in FastAPI's worker threads.
PDF_LOCK = Lock()
TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES & ~pymupdf.TEXT_USE_CID_FOR_UNKNOWN_UNICODE


def extract_pdf(path: Path) -> tuple[str, PdfExtractionMetadata, PdfLayout]:
    source = path.read_bytes()
    try:
        with PDF_LOCK, pymupdf.open(stream=source, filetype="pdf") as document:
            if not document.is_pdf or document.needs_pass or document.is_repaired:
                raise ExtractionError("The stored PDF is damaged or cannot be opened for extraction.")
            pages = []
            text_pages = []
            image_only_pages = []
            has_readable_text = False
            for page in document:
                layout = extract_page(page)
                pages.append(layout)
                text = "\n".join(
                    "".join(span.text for span in line.spans)
                    for block in layout.blocks for line in block.lines
                )
                if text.strip():
                    text_pages.append(text)
                if any(char.isalnum() for char in text):
                    has_readable_text = True
                elif page.get_image_info():
                    image_only_pages.append(layout.page_number)

            if not has_readable_text:
                if image_only_pages:
                    raise ExtractionError("This PDF appears to be scanned or image-only. OCR is not yet supported.")
                raise ExtractionError("The PDF has no readable text.")

            return "\n\n".join(text_pages), PdfExtractionMetadata(len(pages), image_only_pages), PdfLayout(pages)
    except ExtractionError:
        raise
    except (RuntimeError, ValueError) as error:
        raise ExtractionError("The stored PDF could not be read for extraction.") from error


def extract_page(page: pymupdf.Page) -> PdfPage:
    blocks = []
    for block in page.get_text("dict", flags=TEXT_FLAGS, sort=True)["blocks"]:
        if block["type"] != 0:
            continue

        lines = []
        for line in block["lines"]:
            spans = [
                PdfSpan(span["text"], span["bbox"], span["origin"], span["font"], span["size"], span["flags"], span["color"])
                for span in line["spans"]
            ]
            lines.append(PdfLine(line["bbox"], line["dir"], line["wmode"], spans))
        blocks.append(PdfBlock(block["number"], block["bbox"], lines))

    return PdfPage(
        page_number=page.number + 1,
        rotation=page.rotation,
        media_box=tuple(page.mediabox),
        crop_box=tuple(page.cropbox),
        pdf_to_page_transform=tuple(page.transformation_matrix),
        blocks=blocks,
    )
