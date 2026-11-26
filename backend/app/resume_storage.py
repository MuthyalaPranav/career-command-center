import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from .match_models import ResumeOption
from .private_json_storage import write_private_json
from .resume_extraction_models import ResumeExtraction
from .resume_validation import DOCX_TYPE, MAX_RESUME_SIZE, PDF_TYPE, InvalidResume, ResumeTooLarge, validate_resume
from .settings import DATA_DIRECTORY

UPLOAD_DIRECTORY = DATA_DIRECTORY / "private_uploads"
EXTRACTION_DIRECTORY = UPLOAD_DIRECTORY.with_name("private_extractions")
CHUNK_SIZE = 64 * 1024
EXTRACTION_ADAPTER = TypeAdapter(ResumeExtraction)


@dataclass(frozen=True)
class StoredResume:
    resume_id: UUID
    file_type: str
    file_size: int
    original_filename: str


class UploadName(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value):
        if isinstance(value, str):
            return "".join(char for char in value if not unicodedata.category(char).startswith("C"))
        return value


class UploadMetadata(BaseModel):
    original_filename: str | None = None
    name: str | None = None

    @field_validator("original_filename")
    @classmethod
    def filename_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.replace("\\", "/").rsplit("/", 1)[-1].strip() or None


def resume_path(directory: Path, resume_id: UUID, extension: str) -> Path:
    return directory / f"{resume_id}{extension}"


def find_resume(directory: Path, resume_id: UUID) -> Path:
    for extension in (".pdf", ".docx"):
        path = resume_path(directory, resume_id, extension)
        if not path.is_symlink() and path.is_file():
            return path
    raise FileNotFoundError("Resume not found.")


def save_upload_metadata(directory: Path, resume_id: UUID, metadata: UploadMetadata, *, overwrite: bool) -> None:
    destination = resume_path(directory, resume_id, ".name.json")
    write_private_json(destination, metadata.model_dump_json().encode("utf-8"), overwrite=overwrite)


def read_upload_metadata(directory: Path, resume_id: UUID, extension: str) -> UploadMetadata:
    path = resume_path(directory, resume_id, ".name.json")
    if path.is_symlink():
        return UploadMetadata()
    try:
        metadata = UploadMetadata.model_validate_json(path.read_bytes())
    except (FileNotFoundError, IsADirectoryError, ValidationError):
        return UploadMetadata()
    # Older sidecars used the name field for both filenames and custom labels.
    if ("original_filename" not in metadata.model_fields_set and metadata.name
            and metadata.name.lower().endswith(extension)):
        return UploadMetadata(original_filename=metadata.name)
    return metadata


def upload_option(directory: Path, resume_id: UUID, extension: str) -> ResumeOption:
    metadata = read_upload_metadata(directory, resume_id, extension)
    filename = metadata.original_filename
    stem = filename[:-len(extension)] if filename and filename.lower().endswith(extension) else filename
    return ResumeOption(
        id=resume_id, label=metadata.name or stem or f"Resume - {str(resume_id)[:8]}", original_filename=filename,
    )


def rename_upload(directory: Path, resume_id: UUID, extension: str, name: UploadName) -> ResumeOption:
    metadata = read_upload_metadata(directory, resume_id, extension)
    save_upload_metadata(directory, resume_id, metadata.model_copy(update={"name": name.name}), overwrite=True)
    return ResumeOption(id=resume_id, label=name.name, original_filename=metadata.original_filename)


def store_resume(source: BinaryIO, original_filename: str, directory: Path) -> StoredResume:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = None
    file_size = 0
    try:
        with NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := source.read(min(CHUNK_SIZE, MAX_RESUME_SIZE - file_size + 1)):
                file_size += len(chunk)
                if file_size > MAX_RESUME_SIZE:
                    raise ResumeTooLarge("The resume must be 5 MB or smaller.")
                temporary.write(chunk)

        if file_size == 0:
            raise InvalidResume("The resume file is empty.")

        extension, file_type = validate_resume(temporary_path, original_filename)
        resume_id = uuid4()
        name = UploadName(name=original_filename[-255:])
        save_upload_metadata(directory, resume_id, UploadMetadata(original_filename=name.name), overwrite=False)
        try:
            temporary_path.rename(resume_path(directory, resume_id, extension))
        except OSError:
            resume_path(directory, resume_id, ".name.json").unlink(missing_ok=True)
            raise
        return StoredResume(resume_id, file_type, file_size, name.name)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def save_extraction(directory: Path, extraction: ResumeExtraction) -> None:
    payload = EXTRACTION_ADAPTER.dump_json(extraction, indent=2, by_alias=True, exclude_none=True)
    write_private_json(resume_path(directory, extraction.resume_id, ".json"), payload, overwrite=True)


def cached_extraction(directory: Path, source: Path, resume_id: UUID) -> ResumeExtraction | None:
    snapshot = resume_path(directory, resume_id, ".json")
    try:
        if snapshot.is_symlink() or snapshot.stat().st_mtime_ns < source.stat().st_mtime_ns:
            return None
        extraction = EXTRACTION_ADAPTER.validate_json(snapshot.read_bytes())
    except (FileNotFoundError, ValidationError):
        return None
    if extraction.resume_id != resume_id:
        return None
    if source.suffix == ".pdf" and (extraction.file_type != PDF_TYPE or extraction.layout_metadata is None):
        return None
    if source.suffix == ".docx" and extraction.file_type != DOCX_TYPE:
        return None
    return extraction
