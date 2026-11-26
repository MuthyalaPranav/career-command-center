from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from . import generation_storage, match_storage, resume_routes
from .generation_models import GenerationMode, StoredGeneration, VersionContent, VersionSnapshot
from .match_models import ResumeSource
from .pdf_extraction import extract_pdf
from .private_json_storage import write_private_json
from .resume_extraction_models import ExtractionError
from .resume_matching import canonicalize, index_resume, resume_lines
from .resume_sections import ResumeLine, parse_sections
from .resume_storage import find_resume, upload_option
from .resume_validation import PDF_TYPE
from .settings import DATA_DIRECTORY

MASTER_PATH = DATA_DIRECTORY / "private_workspace" / "master.json"
ResumeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ActiveMaster(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_id: UUID


class VersionPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: ResumeName | None = None
    favorite: bool = False


class ResumeVersion(BaseModel):
    generation_id: UUID
    resume_id: UUID
    match_id: UUID
    job_id: UUID | None
    mode: GenerationMode
    job_title: str | None
    original_filename: str | None = None
    created_at: datetime | None
    page_count: int
    skills: list[str] | None
    keywords: list[str] | None
    status: Literal["available", "unavailable"]
    source: Literal["generated", "legacy"]
    name: str | None
    favorite: bool


class VersionDetail(ResumeVersion):
    snapshot: VersionSnapshot


def read_master() -> ActiveMaster | None:
    if MASTER_PATH.is_symlink():
        raise match_storage.InvalidRecord("Active master resume")
    try:
        return ActiveMaster.model_validate_json(MASTER_PATH.read_bytes())
    except FileNotFoundError:
        return None
    except ValidationError as error:
        raise match_storage.InvalidRecord("Active master resume") from error


def save_master(master: ActiveMaster) -> None:
    write_private_json(MASTER_PATH, master.model_dump_json().encode(), overwrite=True)


def legacy_snapshot(metadata: StoredGeneration, directory: Path, path: Path | None) -> VersionSnapshot:
    cached = directory / "legacy-snapshot.json"
    if not cached.is_symlink():
        try:
            return VersionSnapshot.model_validate_json(cached.read_bytes())
        except (FileNotFoundError, ValidationError):
            pass
    job_id = None
    job_title = None
    try:
        report = match_storage.load_match(metadata.match_id).report
        if report.resume_id == metadata.resume_id:
            job_id, job_title = report.job_id, report.job_title
    except (match_storage.RecordNotFound, match_storage.InvalidRecord):
        pass
    content = None
    if path is not None:
        try:
            text, _, _ = extract_pdf(path)
            content = VersionContent(sections=parse_sections(ResumeLine(line) for line in text.splitlines()))
        except (ExtractionError, FileNotFoundError):
            pass
    snapshot = VersionSnapshot(content=content, job_id=job_id, job_title=job_title)
    # An unavailable file can be restored later. Retry indexing when the PDF is readable.
    if content is not None:
        try:
            write_private_json(cached, snapshot.model_dump_json().encode(), overwrite=False)
        except FileExistsError:
            pass
    return snapshot


def read_preferences(directory: Path) -> VersionPreferences:
    path = directory / "preferences.json"
    if path.is_symlink():
        raise match_storage.InvalidRecord("Resume version preferences")
    try:
        return VersionPreferences.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return VersionPreferences()
    except ValidationError as error:
        raise match_storage.InvalidRecord("Resume version preferences") from error


def version_details(metadata: StoredGeneration, directory: Path) -> VersionDetail:
    path = generation_storage.generation_pdf(directory)
    snapshot = metadata.snapshot or legacy_snapshot(metadata, directory, path)
    prefs = read_preferences(directory)
    skills = None
    if snapshot.content is not None:
        source = ResumeSource(resume_id=metadata.resume_id, file_type=PDF_TYPE, sections=snapshot.content.sections)
        skills = sorted(
            (canonicalize(key) for key, evidence in index_resume(resume_lines(source)).items()
             if any(kind == "direct" for kind, _ in evidence)),
            key=str.casefold,
        )
    filename = None
    uploads = resume_routes.UPLOAD_DIRECTORY
    try:
        upload = find_resume(uploads, metadata.resume_id)
        filename = upload_option(uploads, metadata.resume_id, upload.suffix).original_filename
    except FileNotFoundError:
        pass
    return VersionDetail(
        generation_id=metadata.generation_id, resume_id=metadata.resume_id, match_id=metadata.match_id,
        job_id=snapshot.job_id, mode=metadata.mode, job_title=snapshot.job_title, original_filename=filename,
        created_at=snapshot.created_at, page_count=metadata.page_count, skills=skills, keywords=snapshot.keywords,
        status="available" if path is not None and snapshot.content is not None else "unavailable",
        source="generated" if metadata.snapshot else "legacy", snapshot=snapshot,
        **prefs.model_dump(),
    )


def generation_records() -> Iterator[tuple[StoredGeneration, Path]]:
    root = generation_storage.GENERATED_DIRECTORY
    for path in root.iterdir() if root.is_dir() else ():
        try:
            generation_id = UUID(path.name)
            if str(generation_id) != path.name:
                continue
        except ValueError:
            continue
        try:
            yield generation_storage.read_generation_record(generation_id)
        except (match_storage.RecordNotFound, match_storage.InvalidRecord):
            continue
