from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID

import test_pdf_layout_editing as layout_fixture
import test_resume_generation as fixture
from app import match_storage
from app.match_models import GeminiOutput
from app.resume_matching import compare_resume, coverage_breakdown


class ResumeEditorTests(IsolatedAsyncioTestCase):
    setUp = fixture.ResumeGenerationTests.setUp
    generate = fixture.ResumeGenerationTests.generate
    pdf = fixture.ResumeGenerationTests.pdf

    async def comparison(self):
        original = layout_fixture.synthetic_pdf([
            (65, "Summary"), (85, "Python developer."),
            (115, "Skills"), (140, "JS, Python"), (165, "Internal testing tools"),
            (195, "Projects"), (220, "Studying GitHub Actions."),
            (250, "Experience"), (275, "Utilized Python to support releases."),
        ])
        upload = await self.client.post("/api/resumes/upload", files={"file": ("editor.pdf", original)})
        self.assertEqual(upload.status_code, 201, upload.text)
        resume_id = upload.json()["resume_id"]
        self.assertEqual((await self.client.get(f"/api/resumes/{resume_id}/extract")).status_code, 200)
        job = await self.client.post("/api/jobs/analyze", json={"job_description": (
            "Job title: Engineer\nRequired skills: JavaScript, Python, Docker\n"
            "Preferred skills: CI/CD, PostgreSQL\nResponsibilities:\nMaintain release workflows."
        )})
        self.assertEqual(job.status_code, 200, job.text)
        match = await self.client.post("/api/matches", json={"resume_id": resume_id, "job_id": job.json()["job_id"]})
        self.assertEqual(match.status_code, 200, match.text)
        return match.json()

    async def view(self, match):
        response = await self.client.get(f'/api/matches/{match["match_id"]}/editor')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response.json()

    async def test_comparison_groups_real_skills_requirements_and_coverage(self):
        match = await self.comparison()
        view = await self.view(match)
        self.assertEqual(view["skills"]["technical_skills"], ["JavaScript", "Python"])
        self.assertIn("Internal testing tools", view["original_sections"]["technical_skills"])
        self.assertEqual(view["job"]["required_skills"], ["JavaScript", "Python", "Docker"])
        self.assertEqual(view["job"]["preferred_skills"], ["CI/CD", "PostgreSQL"])
        self.assertIn("Maintain release workflows.", view["job"]["responsibilities"])
        self.assertEqual({skill["keyword"] for skill in match["missing_keywords"]}, {"Docker", "PostgreSQL"})
        self.assertEqual({skill["keyword"] for skill in match["partial_matches"]}, {"CI/CD"})
        self.assertEqual(view["current_coverage"]["required"], {"matched": 2, "total": 3, "percentage": 66.7})
        self.assertEqual(view["current_coverage"]["preferred"], {"matched": 0, "total": 2, "percentage": 0.0})
        self.assertEqual(view["current_coverage"]["overall"]["percentage"], 40)
        self.assertEqual(view["preview_sections"], view["original_sections"])

    async def test_manual_wording_must_match_source_and_export_requires_fresh_approval(self):
        match = await self.comparison()
        base = f'/api/matches/{match["match_id"]}'
        with patch("app.resume_optimization.request_gemini", side_effect=lambda data: GeminiOutput(suggestions=[
            {"candidate_id": row["candidate_id"], "suggested_replacement": row["suggested_replacement"]}
            for row in data["candidates"] if row["section"] == "technical_skills"
        ])):
            result = await self.client.post(base + "/optimize")
        self.assertEqual(result.status_code, 200, result.text)
        suggestion = result.json()["optimization"]["suggestions"][0]
        decision = f'{base}/suggestions/{suggestion["suggestion_id"]}'
        edit = decision + "/edit"
        self.assertEqual((await self.client.patch(decision, json={"approval_status": "accepted"})).status_code, 200)
        for unsupported in ("JavaScript, Python, AWS", "JavaScript, Python. Increased revenue by 20%", "No JavaScript, Python"):
            with self.subTest(unsupported=unsupported):
                response = await self.client.put(edit, json={"replacement": unsupported})
                self.assertEqual(response.status_code, 422)
        before = await self.view(match)
        self.assertIn("JavaScript, Python", before["preview_sections"]["technical_skills"])

        revised = "JavaScript, Python."
        saved = await self.client.put(edit, json={"replacement": revised})
        self.assertEqual(saved.status_code, 200, saved.text)
        item = saved.json()["optimization"]["suggestions"][0]
        self.assertEqual((item["manual_replacement"], item["approval_status"]), (revised, "pending"))
        self.assertIn("JS, Python", (await self.view(match))["preview_sections"]["technical_skills"])
        self.assertEqual((await self.client.patch(decision, json={"approval_status": "accepted"})).status_code, 200)
        preview = await self.view(match)
        self.assertIn(revised, preview["preview_sections"]["technical_skills"])
        resume = match_storage.load_resume(UUID(match["resume_id"]))
        job = match_storage.load_job(UUID(match["job_id"]))
        proposed = compare_resume(resume.model_copy(update={"sections": preview["preview_sections"]}), job,
                                  UUID(match["match_id"]), include_candidates=False).report
        self.assertEqual(preview["proposed_coverage"], coverage_breakdown(proposed).model_dump())
        request = {key: match[key] for key in ("match_id", "resume_id")}
        for mode in ("ats", "original_layout"):
            version = await self.generate({**request, "mode": mode})
            self.assertEqual(version["applied_suggestion_count"], 1)
            with await self.pdf(version) as pdf:
                self.assertIn(revised, pdf[0].get_text())
            history = await self.client.get(f'/api/resumes/history/{version["generation_id"]}')
            self.assertEqual(history.json()["snapshot"]["applied_suggestions"][0]["manual_replacement"], revised)
        restored = await self.client.put(edit, json={"replacement": None})
        item = restored.json()["optimization"]["suggestions"][0]
        self.assertIsNone(item["manual_replacement"])
        self.assertEqual(item["approval_status"], "pending")
        self.assertIn("JS, Python", (await self.view(match))["preview_sections"]["technical_skills"])

    async def test_self_reported_evidence_requires_separate_approval_and_protects_coverage(self):
        match = await self.comparison()
        base = f'/api/matches/{match["match_id"]}'
        skills = base + "/skills"
        source = {"status": "already_know", "source": "project"}
        for keyword, text in (("Python", "Used Python for reporting"), ("Docker", "Docker"),
                              ("Docker", "Studying Docker for deployment"), ("Docker", "Used Docker and AWS")):
            result = await self.client.put(skills, json={"keyword": keyword, "evidence": text, **source})
            self.assertEqual(result.status_code, 422)
        evidence = "Used Docker to run a local demo."
        result = await self.client.put(skills, json={"keyword": "Docker", "evidence": evidence, **source})
        self.assertEqual(result.status_code, 200, result.text)
        proposal = result.json()["skill_reviews"]["Docker"]["proposal"]
        self.assertEqual(proposal["verification_status"], "user_attested")
        self.assertEqual(proposal["user_evidence"], {"keyword": "Docker", "source": "project", "text": evidence})
        self.assertEqual(proposal["approval_status"], "pending")
        self.assertNotIn(evidence, (await self.view(match))["preview_sections"]["projects"])
        endpoint = f'{base}/suggestions/{proposal["suggestion_id"]}'
        self.assertEqual((await self.client.put(endpoint + "/edit", json={"replacement": "Used Docker and AWS."})).status_code, 422)
        accepted = await self.client.patch(endpoint, json={"approval_status": "accepted"})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        preview = await self.view(match)
        self.assertIn(evidence, preview["preview_sections"]["projects"])
        self.assertEqual(preview["self_reported_additions"], {"projects": [evidence]})
        self.assertEqual(preview["current_coverage"], preview["proposed_coverage"])
        self.assertNotIn(evidence, preview["original_sections"]["projects"])
        request = {key: match[key] for key in ("match_id", "resume_id")}
        version = await self.generate(request)
        with await self.pdf(version) as pdf:
            self.assertIn(evidence, pdf[0].get_text())
        history = await self.client.get(f'/api/resumes/history/{version["generation_id"]}')
        self.assertEqual(history.json()["snapshot"]["applied_suggestions"][0]["user_evidence"], proposal["user_evidence"])
        layout = await self.client.post("/api/resumes/generate", json={**request, "mode": "original_layout"})
        self.assertEqual(layout.status_code, 422)
        self.assertIn("ATS-friendly export", layout.json()["detail"]["message"])
        self.assertEqual((await self.client.patch(endpoint, json={"approval_status": "rejected"})).status_code, 200)
        self.assertNotIn(evidence, (await self.view(match))["preview_sections"]["projects"])
        for status in ("not_experienced", "review_later"):
            saved = await self.client.put(skills, json={"keyword": "Docker", "status": status})
            self.assertEqual(saved.json()["skill_reviews"]["Docker"], {"status": status, "proposal": None})
