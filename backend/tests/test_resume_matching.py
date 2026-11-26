import asyncio
import json
import os
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from app.job_analysis import analyze_job_description
from app.job_models import StoredJobAnalysis
from app.main import app
from app.match_models import EditCandidate, MatchResult, StoredMatch
from test_resume_extraction import sample_pdf


JOB_TEXT = """Job title: Backend Developer
Required skills: Python, JavaScript, PostgreSQL, CI/CD, Docker
Preferred skills: Terraform, REST API, AWS
Education: Bachelor's degree in Computer Science is required.
Experience: 3 years of experience is required."""


def gemini_response(suggestions):
    return {
        "status": "completed", "steps": [
            {"type": "thought", "content": []},
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(suggestions)}]},
        ],
    }


class MatchingTests(IsolatedAsyncioTestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        root = Path(resources.enter_context(TemporaryDirectory()))
        self.matches = root / "matches"
        self.resumes = root / "extractions"
        self.jobs = root / "analyses"
        self.uploads = root / "uploads"
        for path in (self.resumes, self.jobs, self.uploads):
            path.mkdir()
        self.resume_id, self.job_id = uuid4(), uuid4()
        self.snapshot = self.resumes / f"{self.resume_id}.json"
        self.original = self.uploads / f"{self.resume_id}.pdf"
        self.original.write_bytes(sample_pdf("text"))
        self.snapshot.write_text(json.dumps({
            "resume_id": str(self.resume_id), "file_type": "application/pdf",
            "raw_text": "Private name and address. HiddenContact@example.test",
            "sections": {
                "contact_information": ["HiddenContact@example.test"],
                "professional_summary": ["Utilized JS to develop interfaces."],
                "technical_skills": ["Python, JS, Postgres"],
                "projects": ["Utilized GitHub Actions for build checks.", "Studying Terraform.", "Built a RESTful API."],
                "work_experience": ["Utilized Python for reporting.", "Worked without Docker."],
            },
            "layout_metadata": {"pages": []},
        }), encoding="utf-8")
        job = StoredJobAnalysis(job_id=self.job_id, job_description=JOB_TEXT, **analyze_job_description(JOB_TEXT).model_dump())
        self.job_snapshot = self.jobs / f"{self.job_id}.json"
        self.job_snapshot.write_text(job.model_dump_json(), encoding="utf-8")
        resources.enter_context(patch.multiple(
            "app.match_storage", EXTRACTION_DIRECTORY=self.resumes, JOB_ANALYSIS_DIRECTORY=self.jobs, MATCH_DIRECTORY=self.matches,
        ))
        resources.enter_context(patch("app.resume_routes.UPLOAD_DIRECTORY", self.uploads))
        resources.enter_context(patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-test-key", "GEMINI_MODEL": "test-model"}))
        self.opener = resources.enter_context(patch("app.resume_optimization.build_opener")).return_value
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)

    def compare(self):
        return self.client.post("/api/matches", json={"resume_id": str(self.resume_id), "job_id": str(self.job_id)})

    async def saved_match(self):
        response = await self.compare()
        self.assertEqual(response.status_code, 200, response.text)
        match_id = UUID(response.json()["match_id"])
        path = self.matches / f"{match_id}.json"
        return f"/api/matches/{match_id}", path, StoredMatch.model_validate_json(path.read_bytes())

    def mock_response(self, body):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(body).encode("utf-8")
        self.opener.open.return_value = response
        return response

    async def test_exact_alias_contextual_and_missing_with_evidence(self):
        response = await self.compare()
        self.assertEqual(response.status_code, 200, response.text)
        report = MatchResult.model_validate(response.json())
        self.assertEqual({row.keyword: row.match_type for row in report.matched_skills}, {
            "Python": "exact", "JavaScript": "alias", "PostgreSQL": "alias", "REST API": "alias",
        })
        self.assertEqual({row.keyword for row in report.partial_matches}, {"CI/CD", "Terraform"})
        self.assertEqual({row.keyword for row in report.missing_keywords}, {"AWS", "Docker"})
        self.assertEqual(report.keyword_coverage.model_dump(), {"matched": 4, "total": 8, "percentage": 50.0})
        for item in report.matched_skills + report.partial_matches:
            self.assertTrue(item.jd_evidence)
            self.assertTrue(item.resume_evidence)
            self.assertIn(item.resume_evidence[0].matched_text, item.resume_evidence[0].text)
        self.assertTrue(report.relevant_projects)
        self.assertTrue(report.relevant_experience)
        self.assertEqual(len(report.required_qualifications_needing_review), 2)
        self.assertNotIn("HiddenContact", response.text)
        self.opener.open.assert_not_called()

    async def test_source_list_and_missing_or_invalid_ids(self):
        listing = await self.client.get("/api/matches/sources")
        self.assertEqual(listing.json(), {
            "resumes": [{"id": str(self.resume_id), "label": f"Resume - {str(self.resume_id)[:8]}",
                         "original_filename": None}],
            "jobs": [{"id": str(self.job_id), "label": "Backend Developer"}],
        })
        for resume_id, job_id, status in (
            (str(uuid4()), str(self.job_id), 404), (str(self.resume_id), str(uuid4()), 404),
            ("../../private", str(self.job_id), 422), (str(self.resume_id), "invalid", 422),
        ):
            response = await self.client.post("/api/matches", json={"resume_id": resume_id, "job_id": job_id})
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual((await self.client.get(f"/api/matches/{uuid4()}")).status_code, 404)

    async def test_exact_wording_is_supported_after_the_evidence_limit(self):
        snapshot = json.loads(self.snapshot.read_bytes())
        snapshot["sections"] = {"technical_skills": ["JS"] * 8 + ["JavaScript", "CI / CD"]}
        self.snapshot.write_text(json.dumps(snapshot), encoding="utf-8")
        description = "\n".join(["Required skills: CI/CD"] * 8 + ["Required skills: CI / CD, JavaScript"])
        job = StoredJobAnalysis(
            job_id=self.job_id, job_description=description, **analyze_job_description(description).model_dump(),
        )
        self.job_snapshot.write_text(job.model_dump_json(), encoding="utf-8")

        _, _, record = await self.saved_match()
        self.assertEqual(record.report.keyword_coverage.percentage, 100)
        for row in record.report.matched_skills:
            self.assertEqual(row.match_type, "exact")
            self.assertEqual(row.resume_evidence[0].matched_text, row.jd_evidence[0].matched_text)

    async def test_candidates_preserve_versions_plurals_and_qualifiers(self):
        snapshot = json.loads(self.snapshot.read_bytes())
        snapshot["sections"] = {
            "professional_summary": ["Utilized JS to develop interfaces."],
            "technical_skills": ["JS, HTML5, OAuth2"],
            "projects": ["Utilized RESTful APIs in order to connect services."],
            "work_experience": ["Utilized HTML5 in production.", "Plan to use Docker.", "Worked without Python."],
        }
        self.snapshot.write_text(json.dumps(snapshot), encoding="utf-8")
        description = "Required skills: JavaScript, HTML, OAuth, REST API, Docker, Python"
        job = StoredJobAnalysis(
            job_id=self.job_id, job_description=description, **analyze_job_description(description).model_dump(),
        )
        self.job_snapshot.write_text(job.model_dump_json(), encoding="utf-8")

        _, _, record = await self.saved_match()
        self.assertEqual({item.keyword for item in record.report.partial_matches}, {"Docker"})
        self.assertEqual({item.keyword for item in record.report.missing_keywords}, {"Python"})
        self.assertEqual([item.suggested_replacement for item in record.candidates], [
            "Used JavaScript to develop interfaces.",
            "JavaScript, HTML5, OAuth2",
            "Used RESTful APIs to connect services.",
            "Used HTML5 in production.",
        ])

    async def test_old_unfaithful_candidate_is_filtered_before_calling_gemini(self):
        url, path, record = await self.saved_match()
        record.candidates = [EditCandidate(
            candidate_id=1, section="technical_skills", original_text="HTML5",
            suggested_replacement="HTML", relevant_jd_keywords=["HTML"],
            supporting_resume_evidence=[], reason="Standardize the skill name.",
        )]
        path.write_text(record.model_dump_json(), encoding="utf-8")

        result = await self.client.post(url + "/optimize")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["optimization"]["status"], "no_candidates")
        self.opener.open.assert_not_called()

    async def test_matches_snapshots_created_by_upload_extraction_and_jd_analysis(self):
        content = sample_pdf("text")
        with (
            patch("app.resume_routes.UPLOAD_DIRECTORY", self.uploads),
            patch("app.resume_routes.EXTRACTION_DIRECTORY", self.resumes),
            patch("app.job_routes.JOB_ANALYSIS_DIRECTORY", self.jobs),
        ):
            upload = await self.client.post("/api/resumes/upload", files={"file": ("synthetic.pdf", content)})
            self.assertEqual(upload.status_code, 201, upload.text)
            resume_id = upload.json()["resume_id"]
            extraction = await self.client.get(f"/api/resumes/{resume_id}/extract")
            self.assertEqual(extraction.status_code, 200, extraction.text)
            jd = await self.client.post("/api/jobs/analyze", json={"job_description": "Required skills: Python and FastAPI"})
            self.assertEqual(jd.status_code, 200, jd.text)
            job_id = jd.json()["job_id"]

        sources = [self.uploads / f"{resume_id}.pdf", self.resumes / f"{resume_id}.json", self.jobs / f"{job_id}.json"]
        before = [item.read_bytes() for item in sources]
        response = await self.client.post("/api/matches", json={"resume_id": resume_id, "job_id": job_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["keyword_coverage"]["percentage"], 100)
        self.assertEqual([item.read_bytes() for item in sources], before)

    async def test_verified_output_approval_rejection_and_unchanged_originals(self):
        before = {path: path.read_bytes() for path in (self.snapshot, self.original, self.job_snapshot)}
        url, path, record = await self.saved_match()
        candidate = record.candidates[0]
        self.mock_response(gemini_response({"suggestions": [
            {"candidate_id": candidate.candidate_id, "suggested_replacement": candidate.suggested_replacement},
            {"candidate_id": candidate.candidate_id, "suggested_replacement": "Led 30 employees using Docker."},
            {"candidate_id": 999, "suggested_replacement": "Built an unsupported project."},
        ]}))
        optimized = await self.client.post(url + "/optimize")
        self.assertEqual(optimized.status_code, 200, optimized.text)
        data = optimized.json()["optimization"]
        self.assertEqual(data["discarded_suggestions"], 2)
        self.assertEqual(len(data["suggestions"]), 1)
        suggestion = data["suggestions"][0]
        self.assertEqual(suggestion["suggested_replacement"], candidate.suggested_replacement)
        self.assertEqual(suggestion["verification_status"], "verified_against_source")
        self.assertEqual(suggestion["approval_status"], "pending")

        request = self.opener.open.call_args.args[0]
        sent = json.loads(request.data)
        self.assertFalse(sent["store"])
        self.assertEqual(sent["model"], "test-model")
        self.assertNotIn("HiddenContact", sent["input"])
        self.assertNotIn("synthetic-test-key", optimized.text)
        self.assertEqual(sent["response_format"]["mime_type"], "application/json")
        self.assertEqual(request.get_header("X-goog-api-key"), "synthetic-test-key")

        decision_url = url + f"/suggestions/{suggestion['suggestion_id']}"
        invalid = await self.client.patch(decision_url, json={"approval_status": "pending"})
        self.assertEqual(invalid.status_code, 422)
        unknown = await self.client.patch(url + f"/suggestions/{uuid4()}", json={"approval_status": "accepted"})
        self.assertEqual(unknown.status_code, 404)
        for decision in ("accepted", "rejected"):
            patched = await self.client.patch(decision_url, json={"approval_status": decision})
            self.assertEqual(patched.status_code, 200, patched.text)
            saved = json.loads(path.read_text(encoding="utf-8"))["report"]
            self.assertEqual(saved["optimization"]["suggestions"][0]["approval_status"], decision)
            self.assertEqual((await self.client.get(url)).json(), patched.json())
        self.assertEqual((await self.client.post(url + "/optimize")).json(), saved)
        self.assertEqual((await self.compare()).json(), saved)
        self.opener.open.assert_called_once()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(list(self.matches.glob("*.part")), [])

    async def test_http_failures_empty_candidates_and_invalid_model_response(self):
        url, _, _ = await self.saved_match()
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertEqual((await self.client.post(url + "/optimize")).status_code, 503)
        for problem, expected in (
            (HTTPError("test", 429, "Rate limit", {}, BytesIO()), 429),
            (HTTPError("test", 503, "Unavailable", {}, BytesIO()), 502),
            (URLError(TimeoutError()), 504),
        ):
            self.opener.open.side_effect = problem
            self.assertEqual((await self.client.post(url + "/optimize")).status_code, expected)
        self.opener.open.side_effect = None
        for invalid in (
            {"suggestions": [{"candidate_id": "1", "suggested_replacement": "wrong type"}]},
            {"suggestions": [{"candidate_id": 1, "suggested_replacement": "Invented metric: cut latency 50%."}]},
            {"unexpected": []},
        ):
            self.mock_response(gemini_response(invalid))
            response = await self.client.post(url + "/optimize")
            self.assertEqual(response.status_code, 502, response.text)
            self.assertIsNone((await self.client.get(url)).json()["optimization"])

        for envelope in ({"status": "incomplete"}, {"status": "completed", "steps": []}):
            self.mock_response(envelope)
            self.assertEqual((await self.client.post(url + "/optimize")).status_code, 502)
            self.assertIsNone((await self.client.get(url)).json()["optimization"])

        snapshot = json.loads(self.snapshot.read_bytes())
        snapshot["sections"] = {"technical_skills": ["Python"]}
        self.snapshot.write_text(json.dumps(snapshot), encoding="utf-8")
        empty_url, _, _ = await self.saved_match()
        self.opener.open.reset_mock()
        result = await self.client.post(empty_url + "/optimize")
        self.assertEqual(result.json()["optimization"]["status"], "no_candidates")
        self.opener.open.assert_not_called()

    async def test_concurrent_compare_and_optimize_are_safe(self):
        comparisons = await asyncio.gather(self.compare(), self.compare())
        self.assertTrue(all(response.status_code == 200 for response in comparisons))
        self.assertEqual(comparisons[0].json(), comparisons[1].json())
        url, path, record = await self.saved_match()
        candidate = record.candidates[0]
        mocked = self.mock_response(gemini_response({"suggestions": [{
            "candidate_id": candidate.candidate_id, "suggested_replacement": candidate.suggested_replacement,
        }]}))
        entered, release = Event(), Event()

        def block(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError()
            return mocked

        self.opener.open.side_effect = block
        first = asyncio.create_task(self.client.post(url + "/optimize"))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            concurrent = await self.client.post(url + "/optimize")
            self.assertEqual(concurrent.status_code, 409, concurrent.text)
            self.assertIsNone(json.loads(path.read_bytes())["report"]["optimization"])
        finally:
            release.set()
            result = await first
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((await self.client.post(url + "/optimize")).json(), result.json())
        self.opener.open.assert_called_once()
