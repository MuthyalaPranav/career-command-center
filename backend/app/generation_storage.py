import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .generation_models import GenerationRequest, StoredGeneration
from .match_storage import RecordNotFound, read_record
from .private_json_storage import write_private_json
from .resume_generation import PreparedPDF
from .settings import DATA_DIRECTORY

GENERATED_DIRECTORY = DATA_DIRECTORY / "private_generated_resumes"
PDF_NAME = "resume.pdf"


def save_generation(request: GenerationRequest, pdf: PreparedPDF) -> StoredGeneration:
    GENERATED_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    generation_id = uuid4()
    directory = GENERATED_DIRECTORY / str(generation_id)
    directory.mkdir(mode=0o700)
    path = directory / PDF_NAME
    metadata = StoredGeneration(
        **request.model_dump(), generation_id=generation_id, page_count=pdf.page_count,
        approved_suggestion_count=pdf.approved, applied_suggestion_count=pdf.applied,
        skipped_suggestion_count=pdf.approved - pdf.applied,
        validation=pdf.validation,
        snapshot=pdf.snapshot.model_copy(update={"created_at": datetime.now(timezone.utc)}),
    )
    try:
        with path.open("xb") as file:
            file.write(pdf.data)
            file.flush()
            os.fsync(file.fileno())
        # The atomic metadata file makes the already-complete PDF available.
        write_private_json(directory / f"{generation_id}.json", metadata.model_dump_json(indent=2).encode("utf-8"),
                           overwrite=False)
    except OSError:
        path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return metadata


def generation_directory(generation_id: UUID) -> Path:
    directory = GENERATED_DIRECTORY / str(generation_id)
    if directory.resolve().parent != GENERATED_DIRECTORY.resolve() or directory.is_symlink():
        raise RecordNotFound("Generated resume")
    return directory


def read_generation_record(generation_id: UUID) -> tuple[StoredGeneration, Path]:
    directory = generation_directory(generation_id)
    metadata = read_record(directory, generation_id, StoredGeneration, "generation_id", "Generated resume")
    if (directory / "deleted.json").is_file():
        raise RecordNotFound("Generated resume")
    return metadata, directory


def generation_pdf(directory: Path) -> Path | None:
    path = directory / PDF_NAME
    if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
        return None
    return path


def read_generation(generation_id: UUID) -> tuple[StoredGeneration, Path]:
    metadata, directory = read_generation_record(generation_id)
    path = generation_pdf(directory)
    if path is None:
        raise RecordNotFound("Generated PDF")
    return metadata, path
