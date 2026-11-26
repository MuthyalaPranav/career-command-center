import logging
from contextlib import contextmanager
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response

from . import match_storage as storage
from . import resume_routes
from .match_models import (
    ApprovalRequest, EditorView, ManualEditRequest, MatchRequest, MatchResult, MatchSources, SkillReview,
    SkillReviewRequest, StoredMatch, Suggestion, UserSkillEvidence,
)
from .resume_editor import approval_is_supported, editor_view, suggestions_for
from .resume_matching import attestation_section, attestation_supported, canonicalize, compare_resume
from .resume_optimization import GeminiError, optimize_resume

router = APIRouter(prefix="/api/matches", tags=["matches"])
logger = logging.getLogger(__name__)


@contextmanager
def match_request(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        yield
    except storage.RecordNotFound as error:
        raise HTTPException(status_code=404, detail=f"{error} could not be found.") from error
    except storage.InvalidRecord as error:
        raise HTTPException(status_code=409, detail=f"{error} is invalid. Recreate the source record and try again.") from error
    except storage.MatchBusy as error:
        raise HTTPException(status_code=409, detail="This match is being updated. Try again shortly.") from error
    except GeminiError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except OSError as error:
        logger.error("Failed to access private match data.")
        raise HTTPException(status_code=500, detail="Could not access the match data. Please try again.") from error


@router.get("/sources")
def match_sources(response: Response) -> MatchSources:
    with match_request(response):
        return storage.list_sources(resume_routes.UPLOAD_DIRECTORY)


@router.post("")
def create_match(request: MatchRequest, response: Response) -> MatchResult:
    with match_request(response):
        resume = storage.load_active_resume(resume_routes.UPLOAD_DIRECTORY, request.resume_id)
        storage.require_active_job(request.job_id)
        job = storage.load_job(request.job_id)
        match_id = storage.match_id_for(resume, job)
        try:
            return storage.load_match(match_id).report
        except storage.RecordNotFound:
            record = compare_resume(resume, job, match_id)
        try:
            storage.save_match(record, overwrite=False)
        except FileExistsError:
            return storage.load_match(match_id).report
        return record.report


@router.get("/{match_id}")
def get_match(match_id: UUID, response: Response) -> MatchResult:
    with match_request(response):
        return storage.load_match(match_id).report


@router.get("/{match_id}/editor")
def get_editor(match_id: UUID, response: Response) -> EditorView:
    with match_request(response):
        return editor_view(storage.load_match(match_id))


@router.put("/{match_id}/skills")
def review_skill(match_id: UUID, request: SkillReviewRequest, response: Response) -> MatchResult:
    with match_request(response):
        storage.load_match(match_id)
        with storage.match_update_lock(match_id):
            record = storage.load_match(match_id)
            resume = storage.load_resume(record.report.resume_id)
            job = storage.load_job(record.report.job_id)
            current = compare_resume(resume, job, match_id, include_candidates=False).report
            keyword = canonicalize(request.keyword)
            if not any(item.keyword == keyword for item in (*current.missing_keywords, *current.partial_matches)):
                raise HTTPException(422, "Select a missing or partially matched skill from this comparison.")
            proposal = None
            if request.status == "already_know":
                if request.source is None or request.evidence is None:
                    raise HTTPException(422, "Provide an evidence source and a resume-ready sentence.")
                evidence = UserSkillEvidence(keyword=keyword, source=request.source, text=request.evidence)
                if not attestation_supported(resume, current, evidence):
                    raise HTTPException(422, "Describe how you used this skill in one sentence. Mention other skills only if the resume already supports them.")
                previous = record.report.skill_reviews.get(keyword)
                if previous and previous.proposal and previous.proposal.user_evidence == evidence:
                    return record.report
                proposal = Suggestion(
                    suggestion_id=uuid4(), section=attestation_section(evidence), original_text="",
                    suggested_replacement=evidence.text, supporting_resume_evidence=[], relevant_jd_keywords=[keyword],
                    reason="Include the experience you described after reviewing it for accuracy.",
                    verification_status="user_attested", user_evidence=evidence,
                )
            elif request.source is not None or request.evidence is not None:
                raise HTTPException(422, "Choose Already Know This Skill to submit evidence.")
            record.report.skill_reviews[keyword] = SkillReview(status=request.status, proposal=proposal)
            storage.save_match(record, overwrite=True)
            return record.report


@router.post("/{match_id}/optimize")
def generate_suggestions(match_id: UUID, response: Response) -> MatchResult:
    with match_request(response):
        existing = storage.load_match(match_id)
        if existing.report.optimization is not None:
            return existing.report
        with storage.match_update_lock(match_id):
            record = storage.load_match(match_id)
            if record.report.optimization is None:
                record.report.optimization = optimize_resume(record)
                storage.save_match(record, overwrite=True)
            return record.report


@router.patch("/{match_id}/suggestions/{suggestion_id}")
def decide_suggestion(match_id: UUID, suggestion_id: UUID, request: ApprovalRequest, response: Response) -> MatchResult:
    with match_request(response):
        storage.load_match(match_id)
        with storage.match_update_lock(match_id):
            record = storage.load_match(match_id)
            suggestion = find_suggestion(record, suggestion_id)
            if suggestion.approval_status != request.approval_status:
                if request.approval_status == "accepted" and not approval_is_supported(record, suggestion):
                    raise HTTPException(409, "This suggestion could not be verified against its saved evidence.")
                suggestion.approval_status = request.approval_status
                storage.save_match(record, overwrite=True)
            return record.report


@router.put("/{match_id}/suggestions/{suggestion_id}/edit")
def edit_suggestion(match_id: UUID, suggestion_id: UUID, request: ManualEditRequest, response: Response) -> MatchResult:
    with match_request(response):
        storage.load_match(match_id)
        with storage.match_update_lock(match_id):
            record = storage.load_match(match_id)
            suggestion = find_suggestion(record, suggestion_id)
            if suggestion.verification_status != "verified_against_source":
                raise HTTPException(422, "Update the skill evidence to revise this proposal.")
            if suggestion.manual_replacement != request.replacement:
                suggestion.manual_replacement = request.replacement
                if not approval_is_supported(record, suggestion):
                    raise HTTPException(422, "This wording changes the source claim. Keep the facts, numbers, and skills from the original line.")
                suggestion.approval_status = "pending"
                suggestion.edit_revision += 1
                storage.save_match(record, overwrite=True)
            return record.report


def find_suggestion(record: StoredMatch, suggestion_id: UUID) -> Suggestion:
    for item in suggestions_for(record):
        if item.suggestion_id == suggestion_id:
            return item
    raise HTTPException(404, "Suggestion could not be found for this match.")
