import logging
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from . import generation_storage
from .generation_models import GeneratedResume, GenerationRequest, LayoutCompatibility
from .match_storage import InvalidRecord, MatchBusy, RecordNotFound
from .pdf_extraction import PDF_LOCK
from .pdf_layout_editing import open_pdf, original_pdf, prepare_layout, validate_preview_pdf
from .pdf_layout_validation import LayoutUnsafe
from .resume_generation import MatchMismatch, SourceChanged, load_comparison, prepare_pdf
from .resume_links import SourceLinkError
from .resume_pdf_template import PDFGenerationError
from .resume_storage import UPLOAD_DIRECTORY

router = APIRouter(prefix="/api/resumes", tags=["resumes"])
logger = logging.getLogger(__name__)
PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@contextmanager
def generation_errors():
    try:
        yield
    except RecordNotFound as error:
        raise HTTPException(404, f"{error} could not be found.") from error
    except InvalidRecord as error:
        raise HTTPException(409, f"{error} is invalid.") from error
    except MatchBusy as error:
        raise HTTPException(409, "This record is being updated. Try again shortly.") from error
    except SourceChanged as error:
        raise HTTPException(409, str(error)) from error
    except (MatchMismatch, SourceLinkError, PDFGenerationError, LayoutUnsafe) as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        logger.error("Could not read or write private resume generation data.")
        raise HTTPException(500, "The resume PDF could not be saved or read. Please try again.") from error


@router.post("/generate", status_code=201)
def generate_resume(request: GenerationRequest, response: Response) -> GeneratedResume:
    with generation_errors():
        if request.mode == "original_layout":
            compatibility, pdf = prepare_layout(request, UPLOAD_DIRECTORY)
            if pdf is None:
                raise HTTPException(422, compatibility.model_dump(mode="json"))
        else:
            pdf = prepare_pdf(request, UPLOAD_DIRECTORY)
        metadata = generation_storage.save_generation(request, pdf)
        response.headers.update(PRIVATE_HEADERS)
        return metadata


@router.post("/generate/compatibility")
def resume_layout_compatibility(request: GenerationRequest, response: Response) -> LayoutCompatibility:
    with generation_errors():
        compatibility, _ = prepare_layout(request, UPLOAD_DIRECTORY)
        response.headers.update(PRIVATE_HEADERS)
        return compatibility


@router.get("/{resume_id}/original-pdf", response_class=Response)
def original_resume_pdf(resume_id: UUID, match_id: UUID) -> Response:
    with generation_errors():
        load_comparison(resume_id, match_id)
        request = GenerationRequest(resume_id=resume_id, match_id=match_id)
        data = original_pdf(UPLOAD_DIRECTORY, request).read_bytes()
        validate_preview_pdf(data)
        with PDF_LOCK, open_pdf(data):
            pass
        return Response(data, media_type="application/pdf", headers={
            **PRIVATE_HEADERS, "Content-Disposition": f'inline; filename="original-resume-{resume_id}.pdf"',
        })


@router.get("/generated/{generation_id}")
def generation_metadata(generation_id: UUID, response: Response) -> GeneratedResume:
    with generation_errors():
        metadata, _ = generation_storage.read_generation(generation_id)
        response.headers.update(PRIVATE_HEADERS)
        return metadata


@router.get("/generated/{generation_id}/download", response_class=FileResponse)
def download_pdf(generation_id: UUID, inline: bool = False) -> FileResponse:
    with generation_errors():
        metadata, path = generation_storage.read_generation(generation_id)
        return generated_pdf_response(metadata, path, inline)


def generated_pdf_response(metadata: GeneratedResume, path: Path, inline: bool) -> FileResponse:
    label = "ats-resume" if metadata.mode == "ats" else "layout-resume"
    if metadata.mode == "original_layout":
        validate_preview_pdf(path.read_bytes())
    return FileResponse(
        path, media_type="application/pdf", filename=f"{label}-{metadata.generation_id}.pdf",
        content_disposition_type="inline" if inline else "attachment", headers=PRIVATE_HEADERS,
    )
