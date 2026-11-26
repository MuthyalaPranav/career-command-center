import errno
import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ValidationError

from .job_models import JobAnalysis, StoredJobAnalysis
from .job_storage import JOB_ANALYSIS_DIRECTORY
from .match_models import JobOption, MatchSources, ResumeSource, StoredMatch
from .source_archive import is_archived
from .private_json_storage import write_private_json
from .resume_storage import EXTRACTION_DIRECTORY, upload_option
from .resume_validation import PDF_TYPE
from .settings import DATA_DIRECTORY

MATCH_DIRECTORY = DATA_DIRECTORY / "private_matches"
Model = TypeVar("Model", bound=BaseModel)


class RecordNotFound(LookupError):
    pass


class InvalidRecord(ValueError):
    pass


class MatchBusy(RuntimeError):
    pass


def read_record(directory: Path, record_id: UUID, model: type[Model], id_field: str, label: str) -> Model:
    path = directory / f"{record_id}.json"
    if path.is_symlink():
        raise RecordNotFound(label)
    try:
        record = model.model_validate_json(path.read_bytes())
    except (FileNotFoundError, IsADirectoryError) as error:
        raise RecordNotFound(label) from error
    except ValidationError as error:
        raise InvalidRecord(label) from error
    value = record.report.match_id if isinstance(record, StoredMatch) else getattr(record, id_field)
    if value != record_id:
        raise InvalidRecord(label)
    return record


def load_resume(resume_id: UUID) -> ResumeSource:
    return read_record(EXTRACTION_DIRECTORY, resume_id, ResumeSource, "resume_id", "Resume extraction")


def load_job(job_id: UUID) -> JobAnalysis:
    return read_record(JOB_ANALYSIS_DIRECTORY, job_id, JobAnalysis, "job_id", "Job analysis")


def load_match(match_id: UUID) -> StoredMatch:
    return read_record(MATCH_DIRECTORY, match_id, StoredMatch, "match_id", "Match")


def require_active_job(job_id: UUID) -> None:
    if is_archived(JOB_ANALYSIS_DIRECTORY, job_id):
        raise RecordNotFound("Job analysis")


def load_active_resume(uploads: Path, resume_id: UUID) -> ResumeSource:
    if is_archived(uploads, resume_id):
        raise RecordNotFound("Uploaded resume")
    return load_resume(resume_id)


def list_sources(uploads: Path) -> MatchSources:
    def active_job(job_id: UUID) -> StoredJobAnalysis:
        require_active_job(job_id)
        return read_record(JOB_ANALYSIS_DIRECTORY, job_id, StoredJobAnalysis, "job_id", "Job analysis")

    resumes = []
    jobs = []
    for directory, load in (
        (EXTRACTION_DIRECTORY, lambda source_id: load_active_resume(uploads, source_id)),
        (JOB_ANALYSIS_DIRECTORY, active_job),
    ):
        for path in sorted(directory.glob("*.json")):
            try:
                record_id = UUID(path.stem)
                if path.stem != str(record_id):
                    continue
                source = load(record_id)
            except (ValueError, RecordNotFound):
                continue
            if isinstance(source, ResumeSource):
                extension = ".pdf" if source.file_type == PDF_TYPE else ".docx"
                resumes.append(upload_option(uploads, record_id, extension))
            else:
                company = re.search(r"^[ \t]*(?:company(?: name)?|employer|organi[sz]ation)[ \t]*:[ \t]*([^\r\n]{1,255})", source.job_description, re.I | re.M)
                jobs.append(JobOption(id=record_id, label=source.job_title or "Untitled job description",
                                      company_name=company.group(1).strip() or None if company else None))
    return MatchSources(resumes=resumes, jobs=jobs)


def match_id_for(resume: ResumeSource, job: JobAnalysis) -> UUID:
    snapshot = f"resume-jd-match-v2\n{resume.model_dump_json()}\n{job.model_dump_json()}"
    return uuid5(NAMESPACE_URL, hashlib.sha256(snapshot.encode("utf-8")).hexdigest())


def save_match(record: StoredMatch, *, overwrite: bool) -> None:
    destination = MATCH_DIRECTORY / f"{record.report.match_id}.json"
    write_private_json(destination, record.model_dump_json(indent=2).encode("utf-8"), overwrite=overwrite)


@contextmanager
def match_update_lock(match_id: UUID):
    with record_update_lock(MATCH_DIRECTORY / f"{match_id}.lock"):
        yield


@contextmanager
def record_update_lock(path: Path):
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            def lock():
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock():
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def lock():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        try:
            lock()
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise MatchBusy from error
            raise
        try:
            yield
        finally:
            unlock()
