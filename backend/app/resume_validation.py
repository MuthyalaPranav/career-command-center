import re
import zlib
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import Element, ParseError
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

MAX_RESUME_SIZE = 5 * 1024 * 1024
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCX_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
CONTENT_TYPES_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
RELATIONSHIPS_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
OFFICE_DOCUMENT_RELATIONSHIPS = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
}
WORDPROCESSING_NAMESPACES = (
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}",
    "{http://purl.oclc.org/ooxml/wordprocessingml/main}",
)
MAX_DOCX_ENTRIES = 2_048
MAX_DOCX_EXPANDED_SIZE = 64 * 1024 * 1024
MAX_DOCX_XML_SIZE = 8 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_PDF_OBJECTS = 100_000
PDF_ACTIONS = {"/GoTo", "/URI"}
PDF_UPLOAD_LINK_SCHEMES = {"", "http", "https", "mailto", "tel"}
UNSAFE_PDF_KEYS = {"/AA", "/AF", "/EF", "/EmbeddedFiles", "/JavaScript", "/JS", "/Next", "/XFA"}
UNSAFE_PDF_SUBTYPES = {"/3D", "/FileAttachment", "/Movie", "/RichMedia", "/Screen", "/Sound"}


class InvalidResume(ValueError):
    """An empty or malformed resume."""


class UnsupportedResume(ValueError):
    """An upload with an unsupported filename or file signature."""


class ResumeTooLarge(ValueError):
    """A resume that exceeds the upload size limit."""


def validate_resume(path: Path, original_filename: str) -> tuple[str, str]:
    extension = Path(original_filename).suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise UnsupportedResume("Choose a PDF or DOCX file.")

    with path.open("rb") as document:
        signature = document.read(8)
        if extension == ".pdf" and signature.startswith(b"%PDF-"):
            if not re.fullmatch(rb"%PDF-(?:1\.[0-7]|2\.0)", signature):
                raise InvalidResume("The PDF header is invalid.")
            document.seek(0)
            validate_pdf(document, require_preview_links=False)
            return extension, PDF_TYPE
        if extension == ".docx" and signature.startswith(b"PK\x03\x04"):
            document.seek(0)
            validate_docx(document)
            return extension, DOCX_TYPE

    raise UnsupportedResume("The file contents do not match the selected PDF or DOCX format.")


def validate_pdf(document: BinaryIO, *, require_preview_links: bool = True) -> None:
    try:
        size = document.seek(0, 2)
        document.seek(max(0, size - 1024))
        if not document.read(1024).rstrip(b"\x00\t\n\x0c\r ").endswith(b"%%EOF"):
            raise InvalidResume("The PDF is corrupt or missing its end-of-file marker.")
        document.seek(0)

        reader = PdfReader(document, strict=True)
        if reader.is_encrypted:
            raise InvalidResume("Upload an unencrypted PDF.")
        if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise InvalidResume("The PDF must contain between 1 and 100 pages.")
        validate_pdf_objects(reader.root_object, require_preview_links=require_preview_links)
    except InvalidResume:
        raise
    except (
        PyPdfError, EOFError, OSError, KeyError, TypeError, AttributeError,
        IndexError, ValueError, OverflowError, RecursionError, AssertionError,
    ) as error:
        raise InvalidResume("The PDF is invalid or corrupted.") from error


def validate_pdf_objects(root: DictionaryObject, *, require_preview_links: bool) -> None:
    pending = [root]
    seen = set()
    inspected = 0
    while pending:
        entry = pending.pop()
        inspected += 1
        if inspected > MAX_PDF_OBJECTS:
            raise InvalidResume("The PDF exceeds its internal object limit.")
        if isinstance(entry, IndirectObject):
            key = (entry.idnum, entry.generation)
            if key in seen:
                continue
            seen.add(key)
            entry = entry.get_object()
        if isinstance(entry, DictionaryObject):
            subtype = entry.get("/Subtype")
            if UNSAFE_PDF_KEYS.intersection(entry) or (
                isinstance(subtype, str) and subtype in UNSAFE_PDF_SUBTYPES
            ):
                raise InvalidResume("The PDF contains active content or attachments.")
            for key in ("/A", "/OpenAction"):
                if key not in entry:
                    continue
                action = entry[key]
                if key == "/OpenAction" and isinstance(action, ArrayObject):
                    continue
                if not isinstance(action, DictionaryObject) or action.get("/S") not in PDF_ACTIONS:
                    raise InvalidResume("The PDF contains an unsupported action.")
                if action.get("/S") == "/URI":
                    uri = action["/URI"] if "/URI" in action else None
                    validate_pdf_link(uri, require_preview_links=require_preview_links)
            pending.extend(entry.values())
        elif isinstance(entry, ArrayObject):
            pending.extend(entry)


def validate_pdf_link(uri: object, *, require_preview_links: bool) -> None:
    if not isinstance(uri, str) or re.search(r"[\x00-\x1f\x7f]", uri):
        raise InvalidResume("The PDF contains an unsupported link.")
    try:
        scheme = urlsplit(uri.strip()).scheme
    except ValueError as error:
        raise InvalidResume("The PDF contains an unsupported link.") from error
    if scheme not in PDF_UPLOAD_LINK_SCHEMES:
        raise InvalidResume("The PDF contains an unsupported link.")

    # Uploads can be extracted for ATS export. Only the strict path serves original PDF bytes.
    if require_preview_links and (not re.match(r"(?i)^(https?://|mailto:)", uri) or re.search(r"\s", uri)):
        raise InvalidResume("The PDF contains a link that cannot be used in an original-layout preview.")


def validate_docx(document: BinaryIO) -> None:
    try:
        with ZipFile(document) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_DOCX_ENTRIES
                or sum(entry.file_size for entry in entries) > MAX_DOCX_EXPANDED_SIZE
                or len({entry.filename for entry in entries}) != len(entries)
                or any(
                    entry.flag_bits & 1 or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                    or "\\" in entry.filename or entry.filename.startswith("/")
                    or ".." in entry.filename.split("/")
                    or any(part in {"activex", "embeddings", "vbaproject.bin"} for part in entry.filename.lower().split("/"))
                    for entry in entries
                )
            ):
                raise InvalidResume("The DOCX package is invalid or exceeds its internal limits.")

            content_types = read_docx_xml(archive, "[Content_Types].xml")
            relationships = read_docx_xml(archive, "_rels/.rels")
            if (
                content_types.tag != CONTENT_TYPES_NS + "Types"
                or relationships.tag != RELATIONSHIPS_NS + "Relationships"
            ):
                raise InvalidResume("The DOCX package metadata is invalid.")

            part_name = docx_main_part(relationships)
            registered_part = next((
                part for part in content_types.findall(CONTENT_TYPES_NS + "Override")
                if unquote(part.get("PartName", "")) == f"/{part_name}"
            ), None)
            if registered_part is None:
                extension = part_name.rsplit(".", 1)[-1].lower()
                registered_part = next((
                    part for part in content_types.findall(CONTENT_TYPES_NS + "Default")
                    if part.get("Extension", "").lower() == extension
                ), None)
            if registered_part is None or registered_part.get("ContentType") != DOCX_MAIN_TYPE:
                raise InvalidResume("The archive is not a DOCX document.")

            root = read_docx_xml(archive, part_name)
            if not any(
                root.tag == namespace + "document" and root.find(namespace + "body") is not None
                for namespace in WORDPROCESSING_NAMESPACES
            ):
                raise InvalidResume("The DOCX document body is invalid.")
            if any(
                any(marker in part.get("ContentType", "").lower() for marker in ("macroenabled", "activex", "oleobject"))
                for part in content_types
            ):
                raise InvalidResume("The DOCX contains macros or embedded active content.")
            xml_names = {entry.filename for entry in entries if entry.filename.lower().endswith((".xml", ".rels"))}
            for name in xml_names - {"[Content_Types].xml", "_rels/.rels", part_name}:
                read_docx_xml(archive, name)
            if archive.testzip() is not None:
                raise InvalidResume("The DOCX archive contains a corrupted file.")
    except InvalidResume:
        raise
    except (
        BadZipFile, DefusedXmlException, ParseError, LookupError, EOFError,
        RuntimeError, ValueError, zlib.error,
    ) as error:
        raise InvalidResume("The DOCX is invalid or corrupted.") from error


def read_docx_xml(archive: ZipFile, name: str) -> Element:
    entry = archive.getinfo(name)
    if entry.file_size > MAX_DOCX_XML_SIZE:
        raise InvalidResume("A required DOCX part exceeds its internal size limit.")
    with archive.open(entry) as part:
        return fromstring(part.read(MAX_DOCX_XML_SIZE + 1), forbid_dtd=True)


def docx_main_part(relationships: Element) -> str:
    for relationship in relationships.findall(RELATIONSHIPS_NS + "Relationship"):
        if relationship.get("Type") not in OFFICE_DOCUMENT_RELATIONSHIPS:
            continue
        try:
            target = urlsplit(relationship.get("Target", ""))
        except ValueError as error:
            raise InvalidResume("The DOCX document reference is invalid.") from error
        part_name = unquote(target.path).removeprefix("/").removeprefix("./")
        if (
            relationship.get("TargetMode", "Internal") != "Internal"
            or target.scheme or target.netloc or target.query or target.fragment
            or "\\" in part_name
            or any(segment in {"", ".", ".."} for segment in part_name.split("/"))
        ):
            break
        return part_name
    raise InvalidResume("The DOCX is missing a valid main document reference.")
