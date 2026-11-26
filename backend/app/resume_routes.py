import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic_core import PydanticSerializationError
from python_multipart.exceptions import MultipartParseError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from .resume_extraction import extract_resume
from .resume_extraction_models import ExtractionError, ResumeExtraction
from .resume_storage import EXTRACTION_DIRECTORY, UPLOAD_DIRECTORY, cached_extraction, find_resume, save_extraction, store_resume
from .source_archive import archive_source, is_archived
from .resume_validation import MAX_RESUME_SIZE, InvalidResume, ResumeTooLarge, UnsupportedResume

logger = logging.getLogger(__name__)
# Leave room for multipart headers and boundaries around a file at the exact limit.
MAX_REQUEST_SIZE = MAX_RESUME_SIZE + 64 * 1024
SIZE_LIMIT_MESSAGE = "The resume must be 5 MB or smaller."


class ResumeUploadRoute(APIRoute):
    def get_route_handler(self):
        handle_upload = super().get_route_handler()

        async def handle_bounded_upload(request: Request):
            size = request.headers.get("content-length", "").lstrip("0")
            if size.isascii() and size.isdecimal() and (len(size) > 10 or int(size) > MAX_REQUEST_SIZE):
                raise HTTPException(status_code=413, detail=SIZE_LIMIT_MESSAGE)

            received = 0

            async def receive():
                nonlocal received
                message = await request.receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > MAX_REQUEST_SIZE:
                        # The multipart parser closes its spooled files on this exception.
                        raise MultiPartException(SIZE_LIMIT_MESSAGE)
                return message

            bounded_request = Request(request.scope, receive=receive)
            try:
                async with bounded_request.form(max_files=1, max_fields=0):
                    return await handle_upload(bounded_request)
            except StarletteHTTPException as error:
                if received > MAX_REQUEST_SIZE:
                    raise HTTPException(status_code=413, detail=SIZE_LIMIT_MESSAGE) from error
                raise
            except MultipartParseError as error:
                raise HTTPException(status_code=400, detail="The multipart upload is invalid.") from error

        return handle_bounded_upload


router = APIRouter(prefix="/api/resumes", tags=["resumes"])
upload_router = APIRouter(route_class=ResumeUploadRoute)


class ResumeUploadResponse(BaseModel):
    resume_id: UUID
    original_filename: str
    file_type: str
    file_size: int
    message: str


@upload_router.post("/upload", status_code=status.HTTP_201_CREATED)
def upload_resume(file: Annotated[UploadFile, File()]) -> ResumeUploadResponse:
    original_filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    try:
        stored = store_resume(file.file, original_filename, UPLOAD_DIRECTORY)
    except ResumeTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except UnsupportedResume as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except InvalidResume as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        logger.error("Failed to store a resume upload.")
        raise HTTPException(status_code=500, detail="Unable to save the resume. Please try again.") from error

    return ResumeUploadResponse(
        resume_id=stored.resume_id,
        original_filename=stored.original_filename,
        file_type=stored.file_type,
        file_size=stored.file_size,
        message="Resume uploaded successfully.",
    )


router.include_router(upload_router)


@router.delete("/{resume_id}", status_code=204)
def delete_uploaded_resume(resume_id: UUID) -> Response:
    try:
        find_resume(UPLOAD_DIRECTORY, resume_id)
        archive_source(UPLOAD_DIRECTORY, resume_id)
    except FileNotFoundError as error:
        raise HTTPException(404, "This uploaded resume could not be found.") from error
    except OSError as error:
        logger.error("Failed to archive an uploaded resume.")
        raise HTTPException(500, "Could not delete the uploaded resume. Please try again.") from error
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/{resume_id}/extract", response_model_exclude_none=True)
def extract_uploaded_resume(resume_id: UUID, response: Response) -> ResumeExtraction:
    response.headers["Cache-Control"] = "no-store"
    return load_or_extract_resume(resume_id)


def load_or_extract_resume(resume_id: UUID) -> ResumeExtraction:
    try:
        if is_archived(UPLOAD_DIRECTORY, resume_id):
            raise FileNotFoundError
        snapshot = cached_extraction(EXTRACTION_DIRECTORY, find_resume(UPLOAD_DIRECTORY, resume_id), resume_id)
        if snapshot is not None:
            return snapshot
        extraction = extract_resume(UPLOAD_DIRECTORY, resume_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="This uploaded resume could not be found.") from error
    except ExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        logger.error("Failed to read a stored resume for extraction.")
        raise HTTPException(status_code=500, detail="Unable to extract the resume. Please try again.") from error

    try:
        save_extraction(EXTRACTION_DIRECTORY, extraction)
    except (OSError, PydanticSerializationError) as error:
        logger.error("Failed to save the extracted resume JSON.")
        raise HTTPException(
            status_code=500,
            detail="The resume was extracted, but its JSON could not be saved. Please try again.",
        ) from error

    return extraction
