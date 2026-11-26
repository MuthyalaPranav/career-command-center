from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from . import generation_storage, match_storage, resume_history, resume_routes
from .generation_models import GenerationMode
from .generation_routes import generated_pdf_response, generation_errors
from .match_models import ResumeOption
from .private_json_storage import write_private_json
from .resume_history import ActiveMaster, ResumeVersion, VersionDetail, VersionPreferences
from .source_archive import is_archived
from .resume_storage import UploadName, find_resume, rename_upload, upload_option
from .reuse_recommendations import ReuseRecommendations, rank_versions

router = APIRouter(prefix="/api", tags=["resume history"])


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID


def available_master() -> ActiveMaster | None:
    master = resume_history.read_master()
    if master:
        if is_archived(resume_routes.UPLOAD_DIRECTORY, master.resume_id):
            return None
        try:
            find_resume(resume_routes.UPLOAD_DIRECTORY, master.resume_id)
            match_storage.load_resume(master.resume_id)
        except (FileNotFoundError, match_storage.RecordNotFound, match_storage.InvalidRecord):
            return None
    return master


@router.get("/resumes/master")
def active_master() -> ActiveMaster | None:
    with generation_errors():
        return available_master()


@router.put("/resumes/master")
def change_master(master: ActiveMaster) -> ActiveMaster:
    with generation_errors():
        resume_routes.load_or_extract_resume(master.resume_id)
        resume_history.save_master(master)
        return master


@router.get("/resumes/master/options")
def master_options() -> list[ResumeOption]:
    with generation_errors():
        uploads = resume_routes.UPLOAD_DIRECTORY
        options = []
        for path in sorted(uploads.iterdir()) if uploads.is_dir() else ():
            if path.suffix not in {".docx", ".pdf"} or path.is_symlink() or not path.is_file():
                continue
            try:
                resume_id = UUID(path.stem)
            except ValueError:
                continue
            if str(resume_id) == path.stem and not is_archived(uploads, resume_id):
                options.append(upload_option(uploads, resume_id, path.suffix))
        return options


@router.patch("/resumes/master/options/{resume_id}")
def rename_master_option(resume_id: UUID, name: UploadName) -> ResumeOption:
    with generation_errors():
        uploads = resume_routes.UPLOAD_DIRECTORY
        try:
            if is_archived(uploads, resume_id):
                raise FileNotFoundError
            path = find_resume(uploads, resume_id)
        except FileNotFoundError as error:
            raise match_storage.RecordNotFound("Uploaded resume") from error
        return rename_upload(uploads, resume_id, path.suffix, name)


@router.get("/resumes/history")
def history(
    response: Response,
    q: Annotated[str, Query(max_length=255)] = "",
    mode: GenerationMode | None = None,
    favorite: bool | None = None,
    sort: Literal["newest", "oldest", "name", "favorites"] = "newest",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ResumeVersion]:
    versions = []
    search = q.strip().casefold()
    with generation_errors():
        for stored, directory in resume_history.generation_records():
            if mode is not None and stored.mode != mode:
                continue
            try:
                version = resume_history.version_details(stored, directory)
            except match_storage.InvalidRecord:
                continue
            if favorite is not None and version.favorite != favorite:
                continue
            if search and not any(search in value.casefold() for value in (
                version.name or "", version.job_title or "", *(version.skills or []), *(version.keywords or []),
            )):
                continue
            versions.append(version)

    def ordering(version: ResumeVersion) -> tuple:
        time = version.created_at
        seconds = time.timestamp() if time is not None else 0
        if sort == "name":
            return ((version.name or version.job_title or "Untitled resume").casefold(), version.generation_id)
        return (
            sort == "favorites" and not version.favorite,
            time is None, seconds if sort == "oldest" else -seconds, version.generation_id,
        )

    versions.sort(key=ordering)
    response.headers["X-Total-Count"] = str(len(versions))
    return versions[offset:offset + limit]


@router.get("/resumes/history/{generation_id}")
def history_detail(generation_id: UUID) -> VersionDetail:
    with generation_errors():
        return resume_history.version_details(*generation_storage.read_generation_record(generation_id))


@router.get("/resumes/history/{generation_id}/download", response_class=FileResponse)
def history_download(generation_id: UUID, inline: bool = False) -> FileResponse:
    with generation_errors():
        metadata, path = generation_storage.read_generation(generation_id)
        snapshot = metadata.snapshot or resume_history.legacy_snapshot(metadata, path.parent, path)
        if snapshot.content is None:
            raise match_storage.RecordNotFound("Readable saved resume PDF")
        return generated_pdf_response(metadata, path, inline)


@router.patch("/resumes/history/{generation_id}")
def edit_version(generation_id: UUID, changes: VersionPreferences) -> VersionDetail:
    with generation_errors():
        _, directory = generation_storage.read_generation_record(generation_id)
        with match_storage.record_update_lock(directory / "history.lock"):
            stored, directory = generation_storage.read_generation_record(generation_id)
            prefs = resume_history.read_preferences(directory).model_copy(update=changes.model_dump(exclude_unset=True))
            write_private_json(directory / "preferences.json", prefs.model_dump_json().encode(), overwrite=True)
            return resume_history.version_details(stored, directory)


@router.delete("/resumes/history/{generation_id}", status_code=204)
def delete_version(generation_id: UUID) -> Response:
    with generation_errors():
        try:
            _, directory = generation_storage.read_generation_record(generation_id)
            with match_storage.record_update_lock(directory / "history.lock"):
                generation_storage.read_generation_record(generation_id)
                # Publish the tombstone before the API stops serving this version.
                write_private_json(directory / "deleted.json", b"{}", overwrite=False)
        except match_storage.RecordNotFound:
            pass  # Repeated deletion is safe.
        return Response(status_code=204)


@router.get("/jobs/{job_id}/recommendations")
def recommendations(job_id: UUID) -> ReuseRecommendations:
    with generation_errors():
        job = match_storage.load_job(job_id)
        master = available_master()
        return rank_versions(job, master.resume_id if master else None)


@router.post("/resumes/recommend")
def recommend(request: RecommendationRequest) -> ReuseRecommendations:
    return recommendations(request.job_id)
