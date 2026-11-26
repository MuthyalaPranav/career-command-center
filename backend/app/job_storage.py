from pathlib import Path
from uuid import uuid4

from .job_models import JobAnalysis, JobAnalysisDetails, StoredJobAnalysis
from .private_json_storage import write_private_json
from .settings import DATA_DIRECTORY

JOB_ANALYSIS_DIRECTORY = DATA_DIRECTORY / "private_job_analyses"


def save_job_analysis(directory: Path, job_description: str, details: JobAnalysisDetails) -> JobAnalysis:
    data = details.model_dump()
    for _ in range(3):
        record = StoredJobAnalysis(job_id=uuid4(), job_description=job_description, **data)
        destination = directory / f"{record.job_id}.json"
        try:
            write_private_json(destination, record.model_dump_json(indent=2).encode("utf-8"), overwrite=False)
        except FileExistsError:
            continue
        return JobAnalysis(job_id=record.job_id, **data)
    raise FileExistsError("Could not allocate a unique job analysis ID.")
