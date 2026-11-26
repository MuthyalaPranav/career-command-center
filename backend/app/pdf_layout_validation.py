from collections import Counter
from math import ceil, floor

import pymupdf as pdf

from .generation_models import LayoutValidation

VISUAL_SCALE = 1.5
MAX_PAGE_PIXELS = 8_000_000
MAX_DOCUMENT_PIXELS = 80_000_000


class LayoutUnsafe(ValueError):
    pass


def traced_glyphs(page: pdf.Page) -> Counter:
    return Counter(
        (chr(code), round(x, 2), round(y, 2), span["font"], round(span["size"], 2),
         span["color"], round(span["opacity"], 3), span["type"], span["dir"])
        for span in page.get_texttrace()
        for code, _, (x, y), _ in span["chars"]
        if 0 <= code <= 0x10FFFF and not chr(code).isspace()
    )


def page_annotations(page: pdf.Page) -> tuple:
    links = [{key: value for key, value in link.items() if key not in {"xref", "id"}}
             for link in page.get_links()]
    annotations = [(item.type, item.rect, item.flags, item.info, item.colors, item.vertices)
                   for item in page.annots()]
    widgets = [(item.field_type, item.field_name, item.field_value, item.rect) for item in page.widgets()]
    return links, annotations, widgets


def verify_visual_budget(document: pdf.Document) -> None:
    pixels = [ceil(page.rect.width * VISUAL_SCALE) * ceil(page.rect.height * VISUAL_SCALE) for page in document]
    if (not pixels or len(pixels) > 100 or max(pixels) > MAX_PAGE_PIXELS or sum(pixels) > MAX_DOCUMENT_PIXELS):
        raise LayoutUnsafe("The PDF is too large for the original-design visual checks.")


def compare_images(before: pdf.Page, after: pdf.Page, areas: list[pdf.Rect]) -> None:
    matrix = pdf.Matrix(VISUAL_SCALE, VISUAL_SCALE)
    first = before.get_pixmap(matrix=matrix, colorspace=pdf.csRGB, alpha=False)
    second = after.get_pixmap(matrix=matrix, colorspace=pdf.csRGB, alpha=False)
    if (first.irect, first.stride) != (second.irect, second.stride):
        raise LayoutUnsafe("The rendered page dimensions changed.")
    left, right = first.samples, second.samples
    if not areas:
        if left != right:
            raise LayoutUnsafe(f"Page {before.number + 1} changed outside the approved edit area.")
        return

    regions = [
        (max(0, floor(box.x0 * VISUAL_SCALE) - first.x),
         max(0, floor(box.y0 * VISUAL_SCALE) - first.y),
         min(first.width, ceil(box.x1 * VISUAL_SCALE) - first.x),
         min(first.height, ceil(box.y1 * VISUAL_SCALE) - first.y))
        for box in areas
    ]
    changed = set()
    for y in range(first.height):
        start = y * first.stride
        end = start + first.stride
        if left[start:end] == right[start:end]:
            continue
        intervals = sorted((x0, x1, index) for index, (x0, y0, x1, y1) in enumerate(regions) if y0 <= y < y1)
        cursor = start
        for x0, x1, index in intervals:
            a, b = start + 3 * x0, start + 3 * x1
            if cursor < a and left[cursor:a] != right[cursor:a]:
                raise LayoutUnsafe(f"Page {before.number + 1} changed outside the approved edit area.")
            if left[a:b] != right[a:b]:
                changed.add(index)
            cursor = max(cursor, b)
        if left[cursor:end] != right[cursor:end]:
            raise LayoutUnsafe(f"Page {before.number + 1} changed outside the approved edit area.")
    if len(changed) != len(areas):
        raise LayoutUnsafe("An approved replacement produced no visible change.")


def validate_layout(
    original: pdf.Document, edited: pdf.Document, removed: dict[int, Counter],
    inserted: dict[int, list[tuple[float, float, float, str]]], areas: dict[int, list[pdf.Rect]], warnings: list[str],
) -> LayoutValidation:
    if not edited.is_pdf or edited.is_repaired or edited.needs_pass or len(original) != len(edited):
        raise LayoutUnsafe("The edited PDF could not be reopened with the original page count.")
    for index, before in enumerate(original):
        after = edited[index]
        if (before.mediabox, before.cropbox, before.rect, before.rotation) != (
            after.mediabox, after.cropbox, after.rect, after.rotation,
        ):
            raise LayoutUnsafe("The edited PDF changed the original page geometry.")
        if page_annotations(before) != page_annotations(after):
            raise LayoutUnsafe(f"A link or annotation changed on page {index + 1}.")

        untouched = traced_glyphs(before) - removed.get(index, Counter())
        actual = traced_glyphs(after)
        if untouched - actual:
            raise LayoutUnsafe(f"Unrelated text changed on page {index + 1}.")
        new_glyphs = list((actual - untouched).elements())
        for x0, x1, baseline, text in inserted.get(index, []):
            on_line = [glyph for glyph in new_glyphs if x0 - 0.05 <= glyph[1] <= x1 and
                       abs(glyph[2] - baseline) < 0.06]
            new_glyphs = [glyph for glyph in new_glyphs if glyph not in on_line]
            actual_text = "".join(glyph[0] for glyph in sorted(on_line, key=lambda glyph: glyph[1]))
            if actual_text != "".join(text.split()):
                raise LayoutUnsafe(f"An approved replacement failed the extraction check on page {index + 1}.")
            # Check the PDF's regular text extraction as well as its drawing operators.
            words = after.get_text(
                "rawdict", clip=pdf.INFINITE_RECT(), flags=pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES,
            )["blocks"]
            extracted = [
                char for block in words if block["type"] == 0 for line in block["lines"] for span in line["spans"]
                for char in span["chars"] if x0 - 0.05 <= char["origin"][0] <= x1 and
                abs(char["origin"][1] - baseline) < 0.06 and not char["c"].isspace()
            ]
            if "".join(char["c"] for char in sorted(extracted, key=lambda char: char["origin"][0])) != actual_text:
                raise LayoutUnsafe(f"An approved replacement was not readable on page {index + 1}.")
        if new_glyphs:
            raise LayoutUnsafe(f"Unexpected extractable text appeared on page {index + 1}.")
        compare_images(before, after, areas.get(index, []))
    return LayoutValidation(
        page_count=len(edited), edited_pages=[index + 1 for index in sorted(areas)],
        visually_compared_pages=len(original), warnings=warnings,
    )
