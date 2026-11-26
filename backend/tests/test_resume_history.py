import asyncio
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID, uuid4

import test_pdf_layout_editing as layout_fixture
import test_resume_generation as fixture
from app import match_storage, resume_history
from app.api_protection import APIProtection
from app.main import app
from app.match_models import GeminiOutput
from app.settings import FRONTEND_ORIGINS
from httpx import ASGITransport, AsyncClient
import pymupdf

HISTORY = "/api/resumes/history"
MASTER = "/api/resumes/master"


def synthetic_pdf(skills, *rows):
    return layout_fixture.synthetic_pdf([(80, "Skills"), (105, skills), *rows])


class ResumeHistoryTests(IsolatedAsyncioTestCase):
    prepare = fixture.ResumeGenerationTests.prepare
    generate = fixture.ResumeGenerationTests.generate

    def setUp(self):
        fixture.ResumeGenerationTests.setUp(self)
        master = self.uploads.parent / "workspace" / "master.json"
        manager = patch.object(resume_history, "MASTER_PATH", master)
        manager.start()
        self.addCleanup(manager.stop)

    async def choose_master(self, resume_id):
        response = await self.client.put(MASTER, json={"resume_id": resume_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def recommendations(self, description):
        job = await self.client.post("/api/jobs/analyze", json={"job_description": description})
        self.assertEqual(job.status_code, 200, job.text)
        result = await self.client.post("/api/resumes/recommend", json={"job_id": job.json()["job_id"]})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def test_archive_sources_preserves_edited_exports_and_history(self):
        request = await self.prepare(synthetic_pdf("JS, Python"), "Original.pdf")
        resume_id, match_id = request["resume_id"], request["match_id"]
        await self.choose_master(resume_id)
        match_url = f"/api/matches/{match_id}"
        job_id = (await self.client.get(match_url)).json()["job_id"]
        with patch("app.resume_optimization.request_gemini", side_effect=lambda data: GeminiOutput(suggestions=[
            {"candidate_id": item["candidate_id"], "suggested_replacement": item["suggested_replacement"]}
            for item in data["candidates"]
        ])):
            optimized = (await self.client.post(match_url + "/optimize")).json()
        suggestion = optimized["optimization"]["suggestions"][0]
        decision = f'{match_url}/suggestions/{suggestion["suggestion_id"]}'
        self.assertEqual((await self.client.patch(decision, json={"approval_status": "accepted"})).status_code, 200)
        ats = await self.generate(request)
        manual = suggestion["suggested_replacement"].rstrip(".") + "."
        self.assertEqual((await self.client.put(decision + "/edit", json={"replacement": manual})).status_code, 200)
        self.assertEqual((await self.client.patch(decision, json={"approval_status": "accepted"})).status_code, 200)
        layout = await self.generate({**request, "mode": "original_layout"})
        unrelated = await self.prepare(fixture.sample_resume(minimal=True), "Other.docx")
        history = (await self.client.get(HISTORY)).json()
        urls = [f'{HISTORY}/{result["generation_id"]}' for result in (ats, layout)]
        details = [(await self.client.get(url)).json() for url in urls]
        self.assertEqual(details[1]["snapshot"]["applied_suggestions"][0]["manual_replacement"], manual)
        downloads = [(await self.client.get(url + "/download")).content for url in urls]
        self.assertTrue(all(data.startswith(b"%PDF-") for data in downloads))

        resume_url = f"/api/resumes/{resume_id}"
        job_url = f"/api/jobs/{job_id}"
        for endpoint in (resume_url, job_url):
            self.assertEqual((await self.client.delete(endpoint)).status_code, 204)
            self.assertEqual((await self.client.delete(endpoint)).status_code, 204)
        self.assertIsNone((await self.client.get(MASTER)).json())
        self.assertEqual([item["id"] for item in (await self.client.get(MASTER + "/options")).json()], [unrelated["resume_id"]])
        sources = (await self.client.get("/api/matches/sources")).json()
        self.assertEqual([item["id"] for item in sources["resumes"]], [unrelated["resume_id"]])
        self.assertNotIn(job_id, {item["id"] for item in sources["jobs"]})
        self.assertEqual((await self.client.get(resume_url + "/extract")).status_code, 404)
        self.assertEqual((await self.client.put(MASTER, json={"resume_id": resume_id})).status_code, 404)
        self.assertEqual((await self.client.post("/api/matches", json={"resume_id": resume_id, "job_id": job_id})).status_code, 404)
        self.assertEqual((await self.client.post("/api/matches", json={"resume_id": unrelated["resume_id"], "job_id": job_id})).status_code, 404)
        self.assertEqual((await self.client.get(match_url + "/editor")).status_code, 200)
        self.assertEqual((await self.client.get(HISTORY)).json(), history)
        for url, detail, data in zip(urls, details, downloads, strict=True):
            self.assertEqual((await self.client.get(url)).json(), detail)
            self.assertEqual((await self.client.get(url + "/download?inline=true")).content, data)
            self.assertEqual((await self.client.get(url + "/download")).content, data)
        reuse = await self.recommendations("Required skills: Python, JavaScript")
        self.assertIn(reuse["recommended_generation_id"], {version["generation_id"] for version in (ats, layout)})

    async def test_archive_docx_concurrently_with_export_validates_ids_and_auth(self):
        request = await self.prepare(fixture.sample_resume(minimal=True), "Active.docx")
        job = await self.client.post("/api/jobs/analyze", json={
            "job_description": "Job title: Python Engineer\nCompany: Example Labs\nRequired skills: Python",
        })
        job_id = job.json()["job_id"]
        sources = (await self.client.get("/api/matches/sources")).json()
        option = next(item for item in sources["jobs"] if item["id"] == job_id)
        self.assertEqual(option["company_name"], "Example Labs")
        resume_url = f'/api/resumes/{request["resume_id"]}'
        job_url = f'/api/jobs/{job_id}'
        protected = APIProtection(app, token="test-token", origins=FRONTEND_ORIGINS)
        async with AsyncClient(transport=ASGITransport(app=protected), base_url="http://localhost") as anonymous:
            for url in (resume_url, job_url):
                self.assertEqual((await anonymous.delete(url)).status_code, 401)
        for resource in ("resumes", "jobs"):
            self.assertEqual((await self.client.delete(f"/api/{resource}/invalid")).status_code, 422)
            self.assertEqual((await self.client.delete(f"/api/{resource}/{uuid4()}")).status_code, 404)
        version, first, second, job_delete = await asyncio.gather(
            self.generate(request), self.client.delete(resume_url), self.client.delete(resume_url),
            self.client.delete(job_url),
        )
        self.assertEqual([response.status_code for response in (first, second, job_delete)], [204, 204, 204])
        self.assertEqual((await self.client.delete(f'/api/resumes/{version["generation_id"]}')).status_code, 404)
        self.assertEqual((await self.client.get(MASTER + "/options")).json(), [])
        self.assertEqual((await self.client.get(f'{HISTORY}/{version["generation_id"]}/download')).status_code, 200)
        self.assertTrue((self.uploads / f'{request["resume_id"]}.docx').is_file())

    async def test_master_switch_preserves_uploads_and_both_exports(self):
        self.assertIsNone((await self.client.get(MASTER)).json())
        self.assertEqual((await self.client.put(MASTER, json={"resume_id": str(uuid4())})).status_code, 404)
        first_pdf = synthetic_pdf("JS, Python")
        first = await self.prepare(first_pdf, "first.pdf")
        self.assertEqual(await self.choose_master(first["resume_id"]), {"resume_id": first["resume_id"]})
        ats = await self.generate(first)
        original = await self.generate({**first, "mode": "original_layout"})
        second = await self.prepare(synthetic_pdf("SQL"), "second.pdf")
        await self.choose_master(second["resume_id"])
        self.assertEqual((await self.client.get(MASTER)).json(), {"resume_id": second["resume_id"]})
        versions = (await self.client.get(HISTORY)).json()
        self.assertEqual({v["generation_id"] for v in versions}, {ats["generation_id"], original["generation_id"]})
        for version in versions:
            self.assertEqual(version["resume_id"], first["resume_id"])
            self.assertIsNotNone(version["job_id"])
            self.assertTrue(version["created_at"].endswith("Z"))
            self.assertIn("JavaScript", version["skills"])
            self.assertEqual(version["source"], "generated")
        self.assertEqual((self.uploads / f'{first["resume_id"]}.pdf').read_bytes(), first_pdf)
        detail = (await self.client.get(f'{HISTORY}/{original["generation_id"]}')).json()
        self.assertEqual(detail["snapshot"]["content"]["sections"]["technical_skills"], ["JS, Python"])
        self.assertTrue(all("snapshot" not in version for version in versions))

    async def test_uploaded_resume_can_be_selected_without_a_prior_extraction(self):
        uploaded = await self.client.post("/api/resumes/upload", files={"file": ("sample.pdf", synthetic_pdf("SQL"))})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        resume_id = uploaded.json()["resume_id"]
        options = (await self.client.get(MASTER + "/options")).json()
        self.assertEqual(options, [{"id": resume_id, "label": "sample", "original_filename": "sample.pdf"}])
        snapshot = self.extractions / f"{resume_id}.json"
        self.assertFalse(snapshot.exists())
        await self.choose_master(resume_id)
        self.assertTrue(snapshot.exists())

    async def test_legacy_upload_can_be_renamed_without_changing_its_pdf(self):
        resume_id = str(uuid4())
        self.uploads.mkdir()
        content = synthetic_pdf("Python")
        path = self.uploads / f"{resume_id}.pdf"
        path.write_bytes(content)
        options = (await self.client.get(MASTER + "/options")).json()
        self.assertEqual(options, [{"id": resume_id, "label": f"Resume - {resume_id[:8]}", "original_filename": None}])
        endpoint = f"{MASTER}/options/{resume_id}"
        self.assertEqual((await self.client.patch(endpoint, json={"name": " "})).status_code, 422)
        changed = await self.client.patch(endpoint, json={"name": "  Software engineer resume  "})
        self.assertEqual(changed.json(), {"id": resume_id, "label": "Software engineer resume", "original_filename": None})
        self.assertEqual((await self.client.get(MASTER + "/options")).json(), [changed.json()])
        self.assertEqual(path.read_bytes(), content)

    async def test_duplicate_filenames_keep_separate_ids_and_special_characters_in_both_lists(self):
        filename = "AI & Data - Résumé (2026).PDF"
        contents = [synthetic_pdf("Python"), synthetic_pdf("SQL")]
        ids = []
        for content in contents:
            uploaded = await self.client.post("/api/resumes/upload", files={"file": (filename, content)})
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            self.assertEqual(uploaded.json()["original_filename"], filename)
            resume_id = uploaded.json()["resume_id"]
            ids.append(resume_id)
            self.assertEqual((await self.client.get(f"/api/resumes/{resume_id}/extract")).status_code, 200)
            self.assertEqual((self.uploads / f"{resume_id}.pdf").read_bytes(), content)
        self.assertEqual(len(set(ids)), 2)
        expected = {resume_id: {"id": resume_id, "label": "AI & Data - Résumé (2026)", "original_filename": filename}
                    for resume_id in ids}
        options = (await self.client.get(MASTER + "/options")).json()
        match_sources = (await self.client.get("/api/matches/sources")).json()["resumes"]
        for listing in (options, match_sources):
            self.assertEqual({item["id"]: item for item in listing}, expected)

    async def test_legacy_docx_sidecar_recovers_original_and_rename_keeps_it(self):
        request = await self.prepare(fixture.sample_resume(minimal=True), "Temporary.docx")
        resume_id = request["resume_id"]
        metadata = self.uploads / f"{resume_id}.name.json"
        metadata.write_text(json.dumps({"name": "Python Developer.docx"}), encoding="utf-8")
        options_url = MASTER + "/options"
        recovered = {"id": resume_id, "label": "Python Developer", "original_filename": "Python Developer.docx"}
        self.assertEqual((await self.client.get(options_url)).json(), [recovered])
        renamed = await self.client.patch(f"{MASTER}/options/{resume_id}", json={"name": "Python Developer (master)"})
        expected = {**recovered, "label": "Python Developer (master)"}
        self.assertEqual(renamed.json(), expected)
        self.assertEqual((await self.client.get(options_url)).json(), [expected])
        self.assertEqual(json.loads(metadata.read_text(encoding="utf-8")), {
            "original_filename": "Python Developer.docx", "name": "Python Developer (master)",
        })

    async def test_preferences_and_safe_deletion_keep_other_records(self):
        request = await self.prepare(synthetic_pdf("Python"), "sample.pdf")
        await self.choose_master(request["resume_id"])
        first, second = await self.generate(request), await self.generate(request)
        first_id = first["generation_id"]
        endpoint = f"{HISTORY}/{first_id}"
        manifest = self.generated / first_id / f"{first_id}.json"
        before = manifest.read_bytes()
        response = await self.client.patch(endpoint, json={"name": "  Python application  "})
        self.assertEqual(response.json()["name"], "Python application")
        response = await self.client.patch(endpoint, json={"favorite": True})
        self.assertTrue(response.json()["favorite"])
        self.assertEqual(response.json()["name"], "Python application")
        self.assertEqual((await self.client.patch(endpoint, json={"resume_id": str(uuid4())})).status_code, 422)
        self.assertEqual(manifest.read_bytes(), before)

        for _ in range(2):
            self.assertEqual((await self.client.delete(endpoint)).status_code, 204)
        self.assertEqual((await self.client.get(endpoint)).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/resumes/generated/{first_id}/download")).status_code, 404)
        self.assertEqual((await self.client.get(f"{endpoint}/download")).status_code, 404)
        self.assertEqual((await self.client.get(f'/api/resumes/generated/{second["generation_id"]}/download')).status_code, 200)
        self.assertEqual([row["generation_id"] for row in (await self.client.get(HISTORY)).json()], [second["generation_id"]])
        self.assertEqual((await self.client.get(MASTER)).json()["resume_id"], request["resume_id"])

    async def test_history_query_filters_paginates_and_sorts_with_stable_totals(self):
        empty = await self.client.get(HISTORY)
        self.assertEqual(empty.json(), [])
        self.assertEqual(empty.headers["X-Total-Count"], "0")
        request = await self.prepare(synthetic_pdf("Python"), "sample.pdf")
        oldest = await self.generate(request)
        newest = await self.generate({**request, "mode": "original_layout"})
        await self.client.patch(f'{HISTORY}/{oldest["generation_id"]}', json={"name": "Target Profile", "favorite": True})

        first = await self.client.get(HISTORY, params={"limit": 1, "sort": "newest"}, headers={
            "Origin": "http://localhost:5173",
        })
        self.assertEqual(first.headers["X-Total-Count"], "2")
        self.assertIn("x-total-count", first.headers["Access-Control-Expose-Headers"].lower())
        self.assertEqual([row["generation_id"] for row in first.json()], [newest["generation_id"]])
        next_page = await self.client.get(HISTORY, params={"limit": 1, "offset": 1})
        self.assertEqual([row["generation_id"] for row in next_page.json()], [oldest["generation_id"]])
        for query in ({"q": "profile"}, {"mode": "ats"}, {"favorite": "true"}, {"sort": "oldest", "limit": 1}):
            result = await self.client.get(HISTORY, params=query)
            self.assertEqual([row["generation_id"] for row in result.json()], [oldest["generation_id"]])
        by_title = await self.client.get(HISTORY, params={"q": "developer"})
        self.assertEqual(by_title.headers["X-Total-Count"], "2")
        for query in ({"limit": 101}, {"offset": -1}, {"mode": "docx"}, {"sort": "random"}):
            self.assertEqual((await self.client.get(HISTORY, params=query)).status_code, 422)

    async def test_concurrent_updates_and_deletion_share_the_version_lock(self):
        request = await self.prepare(synthetic_pdf("Python"), "sample.pdf")
        generated = await self.generate(request)
        generation_id = generated["generation_id"]
        directory = self.generated / generation_id
        endpoint = f"{HISTORY}/{generation_id}"
        manifest = directory / f"{generation_id}.json"
        before = manifest.read_bytes()
        with match_storage.record_update_lock(directory / "history.lock"):
            rename, delete = await asyncio.gather(
                self.client.patch(endpoint, json={"name": "Contended name"}), self.client.delete(endpoint),
            )
        self.assertEqual((rename.status_code, delete.status_code), (409, 409))
        self.assertFalse((directory / "preferences.json").exists())
        changes = [{"name": "Saved name"}, {"favorite": True}]
        results = await asyncio.gather(*(self.client.patch(endpoint, json=change) for change in changes))
        for result, change in zip(results, changes):
            self.assertIn(result.status_code, (200, 409))
            if result.status_code == 409:
                self.assertEqual((await self.client.patch(endpoint, json=change)).status_code, 200)
        preferences = json.loads((directory / "preferences.json").read_bytes())
        self.assertEqual(preferences, {"name": "Saved name", "favorite": True})
        self.assertEqual(manifest.read_bytes(), before)

    async def test_recommendation_request_validates_saved_job_and_handles_empty_history(self):
        self.assertEqual((await self.client.post("/api/resumes/recommend", json={"job_id": "invalid"})).status_code, 422)
        self.assertEqual((await self.client.post("/api/resumes/recommend", json={"job_id": str(uuid4())})).status_code, 404)
        self.assertEqual((await self.client.get(f"{HISTORY}/invalid/download")).status_code, 422)
        result = await self.recommendations("Required skills: Python")
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["recommended_generation_id"])
        self.assertEqual(result["recommendation"], "choose_master")

    async def test_reuse_ranks_actual_immutable_content_and_never_calls_gemini(self):
        fixture_rows = [(140, "Projects"), (165, "Built a REST API in Python."),
                        (200, "Education"), (225, "Bachelor of Computer Science")]
        targeted = await self.prepare(synthetic_pdf("JS, Python", *fixture_rows), "targeted.pdf")
        targeted_version = await self.generate(targeted)
        for skill in ("Python", "SQL", "Docker"):
            other = await self.prepare(synthetic_pdf(skill), "other.pdf")
            await self.generate(other)
        await self.choose_master(other["resume_id"])
        extracted = self.extractions / f'{targeted["resume_id"]}.json'
        source = json.loads(extracted.read_bytes())
        source["sections"]["technical_skills"] = ["Docker, Kubernetes"]
        extracted.write_text(json.dumps(source), encoding="utf-8")

        with patch("app.resume_optimization.request_gemini", side_effect=AssertionError("Ranking must be deterministic")):
            result = await self.recommendations(
                "Job title: Engineer\nRequired skills: JavaScript, Python\n"
                "Preferred skills: REST APIs, Docker\nEducation: Bachelor of Computer Science"
            )
            low = await self.recommendations("Required skills: Kubernetes, Rust")
        self.assertEqual(len(result["candidates"]), 3)
        self.assertEqual(result["recommended_generation_id"], None)  # Overall confirmed coverage is 75%, below 80%.
        self.assertEqual(result["recommendation"], "create_from_master")
        best = result["candidates"][0]
        self.assertEqual(best["generation_id"], targeted_version["generation_id"])
        self.assertEqual(best["required_coverage"]["percentage"], 100)
        self.assertEqual(best["preferred_coverage"]["percentage"], 50)
        self.assertEqual({item["keyword"] for item in best["missing_keywords"]}, {"Docker"})
        self.assertEqual({item["keyword"]: item["match_type"] for item in best["matched_keywords"]},
                         {"JavaScript": "alias", "Python": "exact", "REST API": "alias"})
        self.assertIn("JS, Python", best["matched_keywords"][0]["resume_evidence"][0]["text"])
        self.assertTrue(best["relevant_projects"])
        self.assertTrue(best["qualifications_to_review"])
        self.assertTrue(best["qualification_evidence"])
        self.assertEqual(low["recommendation"], "create_from_master")
        self.assertFalse(any(row["suitable"] for row in low["candidates"]))
        with patch("app.reuse_recommendations.REUSE_THRESHOLD", "70"):
            eligible = await self.recommendations(
                "Required skills: JavaScript, Python\nPreferred skills: REST API, Docker"
            )
        self.assertEqual(eligible["recommended_generation_id"], targeted_version["generation_id"])

    async def test_approval_provenance_is_frozen_with_the_rendered_wording(self):
        request = await self.prepare(synthetic_pdf("JS, Python"), "sample.pdf")
        endpoint = f'/api/matches/{request["match_id"]}'
        with patch("app.resume_optimization.request_gemini", side_effect=lambda data: GeminiOutput(suggestions=[
            {"candidate_id": item["candidate_id"], "suggested_replacement": item["suggested_replacement"]}
            for item in data["candidates"]
        ])):
            optimization = await self.client.post(endpoint + "/optimize")
        suggestion = optimization.json()["optimization"]["suggestions"][0]
        decision = f'{endpoint}/suggestions/{suggestion["suggestion_id"]}'
        self.assertEqual((await self.client.patch(decision, json={"approval_status": "accepted"})).status_code, 200)
        versions = [await self.generate({**request, "mode": mode}) for mode in ("ats", "original_layout")]
        self.assertEqual((await self.client.patch(decision, json={"approval_status": "rejected"})).status_code, 200)
        for version in versions:
            entry = f'{HISTORY}/{version["generation_id"]}'
            snapshot = (await self.client.get(entry)).json()["snapshot"]
            self.assertEqual(snapshot["content"]["sections"]["technical_skills"], ["JavaScript, Python"])
            self.assertEqual(snapshot["applied_suggestions"][0]["approval_status"], "accepted")
            self.assertEqual(snapshot["applied_suggestions"][0]["supporting_resume_evidence"][0]["text"], "JS, Python")
            download = await self.client.get(entry + "/download")
            preview = await self.client.get(entry + "/download?inline=true")
            self.assertTrue(download.headers["Content-Disposition"].startswith("attachment"))
            self.assertTrue(preview.headers["Content-Disposition"].startswith("inline"))
            self.assertEqual(download.content, preview.content)
            with pymupdf.open(stream=preview.content, filetype="pdf") as pdf:
                text = "\n".join(page.get_text() for page in pdf)
                self.assertIn(snapshot["content"]["sections"]["technical_skills"][0], text)
                self.assertNotIn("JS, Python", text)

    async def test_legacy_indexes_pdf_text_and_keeps_unknown_metadata_unknown(self):
        request = await self.prepare(synthetic_pdf("FastAPI"), "legacy.pdf")
        generated = await self.generate(request)
        directory = self.generated / generated["generation_id"]
        manifest = directory / f'{generated["generation_id"]}.json'
        manifest.write_text(json.dumps(generated), encoding="utf-8")
        (self.matches / f'{request["match_id"]}.json').unlink()

        first = (await self.client.get(HISTORY)).json()[0]
        self.assertEqual(first["source"], "legacy")
        self.assertIsNone(first["created_at"])
        self.assertIsNone(first["job_id"])
        self.assertIsNone(first["keywords"])
        self.assertEqual(first["skills"], ["FastAPI"])
        cached = directory / "legacy-snapshot.json"
        self.assertTrue(cached.exists())
        before = cached.stat().st_mtime_ns
        self.assertEqual((await self.client.get(HISTORY)).json()[0], first)
        self.assertEqual(cached.stat().st_mtime_ns, before)
        recommended = await self.recommendations("Required skills: FastAPI")
        self.assertEqual(recommended["recommended_generation_id"], generated["generation_id"])
        (directory / "resume.pdf").unlink()
        self.assertEqual((await self.client.get(HISTORY)).json()[0]["status"], "unavailable")
        self.assertEqual((await self.client.get(f'{HISTORY}/{generated["generation_id"]}/download')).status_code, 404)
        unavailable = await self.recommendations("Required skills: FastAPI")
        self.assertEqual(unavailable["recommendation"], "choose_master")
        self.assertEqual(unavailable["candidates"], [])

    async def test_private_routes_require_token_and_allow_cors_for_mutations(self):
        token = "synthetic-workspace-token-for-history-verification"
        protected = APIProtection(app, token, FRONTEND_ORIGINS)
        async with AsyncClient(transport=ASGITransport(app=protected), base_url="http://127.0.0.1") as client:
            for method, route in [
                ("GET", MASTER), ("PUT", MASTER), ("GET", MASTER + "/options"), ("GET", HISTORY),
                ("PATCH", f"{MASTER}/options/{uuid4()}"),
                ("GET", f"{HISTORY}/{uuid4()}"), ("PATCH", f"{HISTORY}/{uuid4()}"),
                ("DELETE", f"{HISTORY}/{uuid4()}"), ("GET", f"/api/jobs/{uuid4()}/recommendations"),
                ("GET", f"{HISTORY}/{uuid4()}/download"), ("POST", "/api/resumes/recommend"),
            ]:
                self.assertEqual((await client.request(method, route)).status_code, 401)
        for method, route in (("PUT", MASTER), ("DELETE", f"{HISTORY}/{uuid4()}")):
            preflight = await self.client.options(route, headers={
                "Origin": "http://localhost:5173", "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            })
            self.assertEqual(preflight.status_code, 200)
