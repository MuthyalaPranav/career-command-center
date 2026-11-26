import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from app.job_analysis import analyze_job_description
from app.job_models import JobAnalysis, MAX_JOB_DESCRIPTION_LENGTH
from app.main import app


SAMPLE_JOB = """Job title: Python Developer
Required skills: Python, FastAPI, SQL, JS, and JavaScript.
Preferred skills: Postgres, GenAI, RESTful APIs, and AWS.
Tools: Git.
Experience: 3+ years of professional experience.
Education: Bachelor’s degree in Computer Science or equivalent.
Responsibilities: Maintain APIs for the café team.
Other qualifications: Strong communication skills.
Docker is not required."""


class JobRulesTests(TestCase):
    def test_extracts_all_document_sections_and_uses_original_evidence(self):
        result = analyze_job_description(SAMPLE_JOB)
        self.assertEqual(result.job_title, "Python Developer")
        self.assertEqual(result.required_skills, ["Python", "FastAPI", "SQL", "JavaScript"])
        self.assertEqual(result.preferred_skills, ["PostgreSQL", "Generative AI", "REST API", "AWS"])
        self.assertEqual(result.tools_and_technologies, ["Git"])
        self.assertEqual(result.experience_requirements, ["3+ years of professional experience."])
        self.assertEqual(result.education_requirements, ["Bachelor’s degree in Computer Science or equivalent."])
        self.assertEqual(result.responsibilities, ["Maintain APIs for the café team."])
        self.assertEqual(result.other_qualifications, ["Strong communication skills."])
        self.assertEqual(result.ats_keywords, result.required_skills + result.preferred_skills + ["Git"])
        self.assertEqual({e.matched_text for e in result.evidence if e.value == "JavaScript"}, {"JS", "JavaScript"})
        self.assertTrue(all(e.snippet in SAMPLE_JOB.splitlines()[e.line_number - 1] for e in result.evidence))
        self.assertTrue(all(e.matched_text in e.snippet for e in result.evidence if e.matched_text))

    def test_classifies_mixed_cues_without_crossing_sentences_or_contrast(self):
        result = analyze_job_description(
            "Python and SQL are required, AWS is preferred. "
            "We require FastAPI but prefer Git. "
            "Docker required, Terraform and Kubernetes preferred; Work with React."
        )
        self.assertEqual(result.required_skills, ["Python", "SQL", "FastAPI", "Docker"])
        self.assertEqual(result.preferred_skills, ["AWS", "Git", "Terraform", "Kubernetes"])
        self.assertEqual(result.tools_and_technologies, ["React"])

    def test_tracks_negation_and_does_not_remove_positive_evidence_elsewhere(self):
        result = analyze_job_description(
            "Required skills:\n- Python. Docker is not required.\n"
            "- No prior experience with React or Vue is required.\n"
            "- Kubernetes isn't required.\n"
            "- Git is not required but preferred.\n"
            "- SQL is desirable but not necessary.\n"
            "- A degree is not required.\n"
            "Preferred skills: Docker."
        )
        self.assertEqual(result.required_skills, ["Python"])
        self.assertEqual(result.preferred_skills, ["Git", "SQL", "Docker"])
        self.assertNotIn("React", result.ats_keywords)
        self.assertNotIn("Vue.js", result.ats_keywords)
        self.assertNotIn("Kubernetes", result.ats_keywords)
        self.assertEqual(result.experience_requirements, [])
        self.assertEqual(result.education_requirements, [])
        self.assertEqual(
            [e.classification for e in result.evidence if e.field == "skill" and e.value == "Docker"],
            ["negated", "preferred"],
        )

    def test_parenthetical_preference_applies_to_the_adjacent_skill(self):
        result = analyze_job_description("**Required skills**: Python, FastAPI and Docker (optional).")
        self.assertEqual(result.required_skills, ["Python", "FastAPI"])
        self.assertEqual(result.preferred_skills, ["Docker"])

    def test_missing_information_stays_empty(self):
        result = analyze_job_description("We are a friendly team. Applications are open.")
        self.assertEqual(result.job_title, None)
        self.assertTrue(all(not value for value in result.model_dump().values()))

    def test_uses_title_like_opening_and_does_not_invent_unknown_skills(self):
        result = analyze_job_description(
            "Senior Backend Engineer\nRequirements:\n- Experience with HyperWidget and NoSQL.\n- Blue Widget proficiency."
        )
        self.assertEqual(result.job_title, "Senior Backend Engineer")
        self.assertEqual(result.required_skills, ["NoSQL"])
        self.assertEqual(result.other_qualifications, ["Blue Widget proficiency."])
        self.assertNotIn("SQL", result.ats_keywords)
        self.assertNotIn("HyperWidget", result.ats_keywords)

    def test_extracts_unheaded_experience_education_and_responsibilities(self):
        result = analyze_job_description(
            "You will build RESTful APIs. 2–4 years of experience with Python required. "
            "A Bachelor’s degree in Computer Science is preferred."
        )
        self.assertIsNone(result.job_title)
        self.assertEqual(result.experience_requirements, ["2–4 years of experience with Python required."])
        self.assertEqual(result.education_requirements, ["A Bachelor’s degree in Computer Science is preferred."])
        self.assertEqual(result.responsibilities, ["You will build RESTful APIs."])
        self.assertEqual(result.required_skills, ["Python"])
        education = next(e for e in result.evidence if e.field == "education_requirements")
        self.assertEqual(education.classification, "preferred")


class JobAnalysisApiTests(IsolatedAsyncioTestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        self.directory = Path(resources.enter_context(TemporaryDirectory())) / "analyses"
        resources.enter_context(patch("app.job_routes.JOB_ANALYSIS_DIRECTORY", self.directory))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)

    async def analyze(self, text):
        return await self.client.post("/api/jobs/analyze", json={"job_description": text})

    async def test_response_and_saved_json_match_and_preserve_source(self):
        original = "\r\n" + SAMPLE_JOB + "\n\n"
        response = await self.analyze(original)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = response.json()
        model = JobAnalysis.model_validate(data)
        self.assertEqual(model.job_id.version, 4)
        saved = self.directory / f"{model.job_id}.json"
        contents = saved.read_text(encoding="utf-8")
        self.assertIn('\n  "job_title": ', contents)
        self.assertIn("café", contents)
        record = json.loads(contents)
        self.assertEqual(record.pop("job_description"), original)
        self.assertEqual(record, data)
        self.assertEqual(list(self.directory.iterdir()), [saved])
        private = await self.client.get(f"/private_job_analyses/{model.job_id}.json")
        self.assertEqual(private.status_code, 404)

    async def test_rejects_invalid_and_empty_descriptions(self):
        for payload in (
            {}, {"job_description": None}, {"job_description": 123}, {"job_description": []},
            {"job_description": ""}, {"job_description": " \t\n "},
            {"job_description": "x" * (MAX_JOB_DESCRIPTION_LENGTH + 1)},
            {"job_description": "Python required", "extra": True},
        ):
            with self.subTest(type=type(payload.get("job_description"))):
                response = await self.client.post("/api/jobs/analyze", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        malformed = await self.client.post("/api/jobs/analyze", content="{broken", headers={"Content-Type": "application/json"})
        self.assertEqual(malformed.status_code, 422)
        self.assertFalse(self.directory.exists())

    async def test_retries_uuid_collision_without_overwriting_existing_analysis(self):
        self.directory.mkdir()
        existing = uuid4()
        replacement = uuid4()
        previous_file = self.directory / f"{existing}.json"
        previous_file.write_text('{"previous": true}', encoding="utf-8")
        with patch("app.job_storage.uuid4", side_effect=[existing, replacement]):
            response = await self.analyze("Python is required.")

        self.assertEqual(response.status_code, 200, response.text)
        returned_id = UUID(response.json()["job_id"])
        self.assertEqual(returned_id, replacement)
        self.assertEqual(previous_file.read_text(encoding="utf-8"), '{"previous": true}')
        self.assertEqual(set(self.directory.iterdir()), {previous_file, self.directory / f"{replacement}.json"})

    async def test_storage_failure_reports_error_and_removes_partial_file(self):
        with (
            self.assertLogs("app.job_routes", level="ERROR"),
            patch("app.private_json_storage.os.fsync", side_effect=OSError(str(self.directory))),
        ):
            response = await self.analyze("Python is required.")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(str(self.directory), response.text)
        self.assertEqual(response.json()["detail"], "Unable to save the job analysis. Please try again.")
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_json_post_cors_preflight(self):
        origin = "http://localhost:5173"
        response = await self.client.options("/api/jobs/analyze", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
