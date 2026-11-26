import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pymupdf as pdf

from . import match_storage
from .generation_models import GenerationRequest, LayoutCompatibility, LayoutIssue, LayoutSource, VersionContent
from .match_models import Suggestion
from .pdf_extraction import PDF_LOCK, TEXT_FLAGS, extract_page
from .pdf_layout_validation import LayoutUnsafe, traced_glyphs, validate_layout, verify_visual_budget
from .resume_generation import (
    PreparedPDF, SourceChanged, approved_attestations, approved_suggestions, edited_sections, load_comparison,
    load_generation_job, resolve_approved, snapshot_for,
)
from .resume_sections import ResumeLine, classify_lines
from .resume_storage import find_resume
from .resume_validation import InvalidResume, validate_pdf

ATS_FALLBACK = "Choose Generate ATS-Friendly PDF if you want to regenerate the resume."
MIN_SIZE = 8


@dataclass
class EditPlan:
    page: int
    redaction: pdf.Rect
    area: pdf.Rect
    lines: list[tuple[pdf.Rect, float, str]]
    font: pdf.Font
    size: float
    color: tuple[float, ...]
    removed: Counter
    warning: str | None


def original_pdf(directory: Path, request: GenerationRequest) -> Path:
    try:
        path = find_resume(directory, request.resume_id)
    except FileNotFoundError as error:
        raise match_storage.RecordNotFound("Original uploaded resume") from error
    if path.suffix != ".pdf":
        raise LayoutUnsafe("This resume was uploaded as a DOCX. Original-design editing requires an uploaded PDF.")
    return path


def open_pdf(data: bytes) -> pdf.Document:
    try:
        document = pdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as error:
        raise LayoutUnsafe("The original PDF is corrupted or unreadable.") from error
    if not document.is_pdf or document.is_repaired or document.needs_pass or not len(document):
        document.close()
        raise LayoutUnsafe("The PDF is corrupted, encrypted, or has no pages.")
    return document


def validate_preview_pdf(data: bytes) -> None:
    try:
        validate_pdf(BytesIO(data))
    except InvalidResume as error:
        raise LayoutUnsafe(str(error)) from error


def pdf_lines(layout):
    return [(index, line, "".join(span.text for span in line.spans))
            for index, page in enumerate(layout.pages) for block in page.blocks for line in block.lines]


def section_references(lines, sections):
    references = {}
    positions: Counter = Counter()
    for index, section, text in classify_lines(ResumeLine(item[2]) for item in lines):
        number = positions[section]
        positions[section] += 1
        if number >= len(sections.get(section, [])) or sections[section][number] != text:
            raise LayoutUnsafe("The saved PDF positions do not match the extracted text. Extract the resume again.")
        references[section, number] = lines[index]
    if any(len(lines) != positions[section] for section, lines in sections.items()):
        raise LayoutUnsafe("The saved PDF positions do not match the extracted sections. Extract the resume again.")
    return references


def font_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.split("+", 1)[-1].casefold())


def font_for(page: pdf.Page, name: str, text: str) -> tuple[pdf.Font, str | None]:
    if any(unicodedata.bidirectional(char) in {"R", "AL", "AN"} or unicodedata.combining(char) for char in text):
        raise LayoutUnsafe("This replacement requires text shaping that the original-design editor cannot reproduce.")
    normalized = font_name(name)
    matching = {entry[0] for entry in page.get_fonts(full=True) if font_name(entry[3]) == normalized}
    if len(matching) > 1:
        raise LayoutUnsafe("Several PDF font resources match this text, so the original font cannot be verified.")
    if matching:
        _, extension, _, contents = page.parent.extract_font(matching.pop())
        if contents and extension in {"ttf", "otf", "cff"}:
            try:
                embedded = pdf.Font(fontbuffer=contents)
                if embedded.is_writable and all(embedded.has_glyph(ord(char), fallback=False) for char in text):
                    return embedded, None
            except (RuntimeError, ValueError):
                pass

    base = {font_name(label): alias for alias, label in pdf.Base14_fontdict.items()
            if label not in {"Symbol", "ZapfDingbats"}}
    equivalent = normalized
    for source, target in (("arial", "helvetica"), ("timesnewroman", "times"), ("couriernew", "courier")):
        if normalized.startswith(source):
            style = normalized[len(source):].removesuffix("psmt").removesuffix("mt")
            style = style.replace("italic", "italic" if target == "times" else "oblique")
            equivalent = target + (style or ("roman" if target == "times" else ""))
            break
    if equivalent in base:
        font = pdf.Font(base[equivalent])
        if all(font.has_glyph(ord(char), fallback=False) for char in text):
            warning = f"Page {page.number + 1} used the compatible {font.name} font for {name}." if (
                equivalent != normalized or contents_available(page, name)
            ) else None
            return font, warning
    raise LayoutUnsafe(f"The PDF font {name} is missing, unsupported, or lacks glyphs required by the edit.")


def contents_available(page: pdf.Page, name: str) -> bool:
    return any(entry[1] != "n/a" for entry in page.get_fonts(full=True) if font_name(entry[3]) == font_name(name))


def wrap_text(text: str, font: pdf.Font, size: float, width: float) -> list[str]:
    rows = []
    for paragraph in text.splitlines():
        row = ""
        for word in paragraph.split():
            next_row = f"{row} {word}" if row else word
            if font.text_length(next_row, fontsize=size) <= width:
                row = next_row
            elif row and font.text_length(word, fontsize=size) <= width:
                rows.append(row)
                row = word
            else:
                raise LayoutUnsafe("The replacement exceeds the available width at the original font size.")
        if row:
            rows.append(row)
    if not rows:
        raise LayoutUnsafe("The replacement has no readable text.")
    return rows


def plan_edit(page: pdf.Page, saved_line, full_text: str, suggestion: Suggestion) -> EditPlan:
    if page.rotation or saved_line.writing_mode or saved_line.direction != (1.0, 0.0):
        raise LayoutUnsafe("Rotated, vertical, or angled text cannot be safely replaced.")
    if any(annot.type[0] == pdf.PDF_ANNOT_REDACT for annot in page.annots()):
        raise LayoutUnsafe("The page already contains a redaction annotation.")
    lines = [
        line for block in page.get_text("rawdict", flags=TEXT_FLAGS, sort=True)["blocks"] if block["type"] == 0
        for line in block["lines"]
    ]
    mentions = [
        line for line in lines if tuple(line["bbox"]) == saved_line.bbox
        and "".join(char["c"] for span in line["spans"] for char in span["chars"]) == full_text
    ]
    if len(mentions) != 1:
        raise LayoutUnsafe("The source text is ambiguous or its exact position could not be confirmed.")
    line = mentions[0]
    chars = [(char, span) for span in line["spans"] for char in span["chars"]]
    end = len(full_text.rstrip())
    start = end - len(suggestion.original_text)
    if start < 0 or full_text[start:end] != suggestion.original_text:
        raise LayoutUnsafe("The approved original text does not match the saved location.")
    target = chars[start:end]
    style = {
        (span["font"], span["size"], span["color"], span.get("alpha", 255), round(char["origin"][1], 2))
        for char, span in target if not char["c"].isspace()
    }
    if len(style) != 1:
        raise LayoutUnsafe("The source uses mixed fonts, sizes, colors, or baselines across its spans.")
    name, size, color, alpha, _ = style.pop()
    x, baseline = target[0][0]["origin"]
    if size < MIN_SIZE or alpha != 255:
        raise LayoutUnsafe("The original text is too small or translucent for safe replacement.")
    old = pdf.Rect()
    for char, _ in target:
        old |= pdf.Rect(char["bbox"])
    target_origins = {(char["c"], round(char["origin"][0], 2), round(char["origin"][1], 2))
                      for char, _ in target if not char["c"].isspace()}
    removed = Counter({glyph: count for glyph, count in traced_glyphs(page).items() if glyph[:3] in target_origins})
    if (len(target_origins) != sum(removed.values()) or
            any(glyph[6] != 1 or glyph[7] != 0 or glyph[8] != (1, 0) for glyph in removed)):
        raise LayoutUnsafe("The source has ambiguous, hidden, or overprinted glyphs at the saved position.")

    font, warning = font_for(page, name, suggestion.replacement_text)
    other_lines = [pdf.Rect(item["bbox"]) for item in lines if item is not line]
    margin = min((box.x0 for box in [old, *other_lines] if 12 <= box.x0 <= x + 1), default=12)
    right = max(old.x1, page.rect.x1 - margin)
    for box in other_lines:
        if box.x0 >= old.x1 - 0.05 and box.y0 < old.y1 + size and box.y1 > old.y0 - size:
            right = min(right, box.x0 - 2)
    # The prefix (such as "Skills:") stays at its existing position.
    has_prefix = bool(full_text[:start].strip())
    centered = not has_prefix and abs((old.x0 + old.x1) / 2 - page.rect.width / 2) <= 2
    if centered:
        right = min(right, page.rect.x1 - old.x0) if right < old.x1 else right
        width = 2 * min(page.rect.width / 2 - margin, right - page.rect.width / 2)
    else:
        width = right - x
    rows = wrap_text(suggestion.replacement_text, font, size, width - 1)
    redaction = pdf.Rect(old.x0 + 0.01, baseline - 0.48 * size, old.x1 - 0.01, baseline - 0.25 * size)
    surrounds = [pdf.Rect(char["bbox"]) for char, _ in chars[:start] + chars[end:] if not char["c"].isspace()]
    if any(redaction.intersects(box) for box in [*other_lines, *surrounds]):
        raise LayoutUnsafe("Removing the source text would affect neighboring text.")

    protected = [link["from"] for link in page.get_links()]
    protected.extend(item.rect for item in page.annots())
    protected.extend(item.rect for item in page.widgets())
    if any(old.intersects(box) for box in protected):
        raise LayoutUnsafe("The edit touches a link, annotation, or form field.")
    graphics = [pdf.Rect(image["bbox"]) for image in page.get_image_info()]
    graphics.extend(item["rect"] for item in page.get_drawings())
    advance = max(size * 1.28, old.height + 0.5)
    placements = []
    area = pdf.Rect(old)
    for index, text in enumerate(rows):
        length = font.text_length(text, fontsize=size)
        left = page.rect.width / 2 - length / 2 if centered else x
        y = baseline + advance * index
        box = pdf.Rect(left, y + old.y0 - baseline, left + length + 0.5, y + old.y1 - baseline)
        if (not page.rect.contains(box) or any(box.intersects(item) for item in [*other_lines, *surrounds, *protected])
                or any((box & item).get_area() > (old & item).get_area() + 0.01 for item in graphics)):
            raise LayoutUnsafe("The replacement would overflow or overlap neighboring PDF content at the original font size.")
        placements.append((box, y, text))
        area |= box
    return EditPlan(page.number, redaction, area + (-2, -2, 2, 2), placements, font, size,
                    tuple(pdf.sRGB_to_pdf(color)), removed, warning)


def issue_for(suggestion: Suggestion, reason: str) -> LayoutIssue:
    return LayoutIssue(suggestion_id=suggestion.suggestion_id, original_text=suggestion.original_text,
                       suggested_replacement=suggestion.replacement_text, reason=reason)


def prepare_layout(request: GenerationRequest, directory: Path) -> tuple[LayoutCompatibility, PreparedPDF | None]:
    match = load_comparison(request.resume_id, request.match_id)
    approved = approved_suggestions(match)
    attested = approved_attestations(match)
    preview = False
    try:
        path = original_pdf(directory, request)
        original = path.read_bytes()
        validate_preview_pdf(original)
        with PDF_LOCK, open_pdf(original) as source:
            preview = True
            if attested:
                message = "The approved evidence adds new lines that require an ATS-friendly export."
                return incompatible([*approved, *attested], preview, message,
                                    [issue_for(item, message) for item in attested]), None
            verify_visual_budget(source)
            if not any(page.get_text().strip() for page in source):
                raise LayoutUnsafe("The PDF is scanned, image-only, or contains text drawn as graphics.")
            if source.get_sigflags() > 0:
                raise LayoutUnsafe("Editing a digitally signed PDF could invalidate its signature.")
            resume = match_storage.read_record(match_storage.EXTRACTION_DIRECTORY, request.resume_id, LayoutSource,
                                               "resume_id", "Resume extraction")
            layout = resume.layout_metadata
            if layout is None or [extract_page(page) for page in source] != layout.pages:
                raise LayoutUnsafe("The saved PDF positions are missing or stale. Extract the resume again.")
            references = section_references(pdf_lines(layout), resume.sections)
            job = load_generation_job(match, approved)
            resolved = resolve_approved(resume, match, approved, job=job)
            accepted = {suggestion.suggestion_id: position for position, suggestion in resolved.items()}
            issues = []
            plans = []
            for suggestion in approved:
                try:
                    if suggestion.suggestion_id not in accepted:
                        raise LayoutUnsafe("The approved edit could not be verified against the source and stored candidate.")
                    index, saved_line, text = references[accepted[suggestion.suggestion_id]]
                    plan = plan_edit(source[index], saved_line, text, suggestion)
                    if any(previous.page == index and any(
                        box.intersects(other) for box, _, _ in plan.lines for other, _, _ in previous.lines
                    ) for previous in plans):
                        raise LayoutUnsafe("Two approved replacements would overlap.")
                    plans.append(plan)
                except LayoutUnsafe as error:
                    issues.append(issue_for(suggestion, str(error)))
            if issues:
                return incompatible(approved, preview, "At least one approved edit cannot be safely applied.", issues), None

            data, report = edit_and_validate(original, source, plans)
        if plans:
            validate_preview_pdf(data)
        if path.read_bytes() != original:
            raise SourceChanged("The original resume changed during generation. Extract it again before retrying.")
    except LayoutUnsafe as error:
        return incompatible(approved, preview, str(error), [issue_for(item, str(error)) for item in approved]), None
    except RuntimeError:
        message = "PyMuPDF could not safely edit or validate this PDF."
        return incompatible(approved, preview, message, [issue_for(item, message) for item in approved]), None

    message = "The approved edits passed the layout checks." if plans else "No edits are approved. A copy of the original is available."
    snapshot = snapshot_for(VersionContent(sections=edited_sections(resume, resolved)), match, job, list(resolved.values()))
    return LayoutCompatibility(compatible=True, message=message, approved_suggestion_count=len(approved),
                               original_preview_available=True, validation=report), PreparedPDF(
        data, report.page_count, len(approved), len(plans), snapshot, report,
    )


def incompatible(approved, preview, message, issues) -> LayoutCompatibility:
    return LayoutCompatibility(compatible=False, message=f"{message} {ATS_FALLBACK}",
                               approved_suggestion_count=len(approved), original_preview_available=preview, issues=issues)


def edit_and_validate(original: bytes, source: pdf.Document, plans: list[EditPlan]):
    removed: dict[int, Counter] = defaultdict(Counter)
    inserted = defaultdict(list)
    areas = defaultdict(list)
    warnings = list(dict.fromkeys(plan.warning for plan in plans if plan.warning))
    data = original
    if plans:
        with open_pdf(original) as edited:
            by_page = defaultdict(list)
            for plan in plans:
                by_page[plan.page].append(plan)
            for index, edits in by_page.items():
                page = edited[index]
                for plan in edits:
                    page.add_redact_annot(plan.redaction, fill=False, cross_out=False)
                page.apply_redactions(images=pdf.PDF_REDACT_IMAGE_NONE, graphics=pdf.PDF_REDACT_LINE_ART_NONE,
                                      text=pdf.PDF_REDACT_TEXT_REMOVE)
                for plan in edits:
                    writer = pdf.TextWriter(page.rect)
                    for box, y, text in plan.lines:
                        writer.append((box.x0, y), text, font=plan.font, fontsize=plan.size)
                        inserted[index].append((box.x0, box.x1, y, text))
                    writer.write_text(page, color=plan.color)
                    removed[index].update(plan.removed)
                    areas[index].append(plan.area)
            data = edited.tobytes(garbage=3, deflate=True)
    with open_pdf(data) as edited:
        report = validate_layout(source, edited, removed, inserted, areas, warnings)
    return data, report
