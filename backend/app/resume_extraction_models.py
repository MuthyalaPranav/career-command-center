from dataclasses import dataclass
from typing import Literal
from uuid import UUID

BoundingBox = tuple[float, float, float, float]
Point = tuple[float, float]
Transform = tuple[float, float, float, float, float, float]


class ExtractionError(ValueError):
    """A stored resume could not be read as a text document."""


@dataclass(frozen=True)
class PdfSpan:
    text: str
    bbox: BoundingBox
    origin: Point
    font: str
    font_size: float
    font_flags: int
    color: int


@dataclass(frozen=True)
class PdfLine:
    bbox: BoundingBox
    direction: Point
    writing_mode: int
    spans: list[PdfSpan]


@dataclass(frozen=True)
class PdfBlock:
    block_number: int
    bbox: BoundingBox
    lines: list[PdfLine]


@dataclass(frozen=True)
class PdfPage:
    page_number: int
    rotation: int
    media_box: BoundingBox
    crop_box: BoundingBox
    pdf_to_page_transform: Transform
    blocks: list[PdfBlock]


@dataclass(frozen=True)
class PdfLayout:
    pages: list[PdfPage]
    coordinate_system: Literal["unrotated_pymupdf_points"] = "unrotated_pymupdf_points"


@dataclass(frozen=True)
class PdfExtractionMetadata:
    page_count: int
    image_only_pages: list[int]
    reading_order: Literal["visual_top_left"] = "visual_top_left"


@dataclass(frozen=True)
class TableCell:
    table: int
    row: int
    column: int


@dataclass(frozen=True)
class DocxParagraph:
    paragraph_number: int
    text: str
    style: str | None = None
    heading_level: int | None = None
    table_path: tuple[TableCell, ...] = ()


@dataclass(frozen=True)
class DocxExtractionMetadata:
    paragraphs: list[DocxParagraph]
    table_count: int
    reading_order: Literal["document_order"] = "document_order"


@dataclass(frozen=True)
class ResumeExtraction:
    resume_id: UUID
    file_type: str
    raw_text: str
    sections: dict[str, list[str]]
    extraction_metadata: PdfExtractionMetadata | DocxExtractionMetadata
    layout_metadata: PdfLayout | None = None
