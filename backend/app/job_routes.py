import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic_core import PydanticSerializationError

from .match_storage import InvalidRecord, RecordNotFound, read_record
from .source_archive import archive_source
from .job_models import StoredJobAnalysis
from .job_analysis import analyze_job_description
from .job_models import JobAnalysis, JobAnalysisRequest
from .job_storage import JOB_ANALYSIS_DIRECTORY, save_job_analysis

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


@router.post("/analyze")
def analyze_job(request: JobAnalysisRequest, response: Response) -> JobAnalysis:
    details = analyze_job_description(request.job_description)
    try:
        analysis = save_job_analysis(JOB_ANALYSIS_DIRECTORY, request.job_description, details)
    except (OSError, PydanticSerializationError) as error:
        logger.error("Failed to save the job description analysis.")
        raise HTTPException(status_code=500, detail="Unable to save the job analysis. Please try again.") from error

    response.headers["Cache-Control"] = "no-store"
    return analysis


@router.delete("/{job_id}", status_code=204)
def delete_job_analysis(job_id: UUID) -> Response:
    try:
        read_record(JOB_ANALYSIS_DIRECTORY, job_id, StoredJobAnalysis, "job_id", "Job analysis")
        archive_source(JOB_ANALYSIS_DIRECTORY, job_id)
    except RecordNotFound as error:
        raise HTTPException(404, "This job analysis could not be found.") from error
    except InvalidRecord as error:
        raise HTTPException(409, "This job analysis is invalid.") from error
    except OSError as error:
        logger.error("Failed to archive a job analysis.")
        raise HTTPException(500, "Could not delete the job analysis. Please try again.") from error
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
