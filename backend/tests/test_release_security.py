import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from uuid import UUID, uuid4
from zipfile import ZipFile

import pymupdf
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from pypdf.annotations import Link

import test_resume_generation as fixture
from test_pdf_layout_editing import synthetic_pdf
from app import match_storage, settings
from app.api_protection import APIProtection
from app.main import app
from app.match_models import OptimizationResult, Suggestion
from test_resume_upload import sample_docx, sample_pdf

TOKEN = "synthetic-test-token-with-no-production-access-123456"


class ProductionConfigurationTests(TestCase):
    def test_required_settings_and_exact_origins(self):
        with TemporaryDirectory() as directory:
            env = {
                **os.environ, "RESUME_ENV": "production", "RESUME_WORKSPACE_TOKEN": TOKEN,
                "RESUME_DATA_DIR": directory, "GEMINI_API_KEY": "synthetic-provider-key",
                "GEMINI_MODEL": "gemini-test", "FRONTEND_ORIGINS": "https://resume.example.test",
            }
            backend = Path(__file__).resolve().parents[1]
            for key, invalid in (
                ("RESUME_WORKSPACE_TOKEN", ""), ("RESUME_DATA_DIR", "relative-path"),
                ("GEMINI_API_KEY", ""), ("FRONTEND_ORIGINS", "*"),
                ("FRONTEND_ORIGINS", "http://resume.example.test"),
                ("FRONTEND_ORIGINS", "https://resume.example.test/path"),
            ):
                with self.subTest(key=key, invalid=invalid):
                    result = subprocess.run(
                        [sys.executable, "-c", "from app.main import app"], cwd=backend,
                        env={**env, key: invalid}, capture_output=True, text=True, timeout=20,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn(TOKEN, result.stderr)
            check = """
import asyncio
from httpx import ASGITransport, AsyncClient
from app.main import app

async def check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.example.test") as client:
        assert (await client.get("/api/health")).json() == {"status": "ok"}
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404
        assert (await client.get("/api/access")).status_code == 401

asyncio.run(check())
"""
            result = subprocess.run(
                [sys.executable, "-c", check], cwd=backend, env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


class ReleaseSecurityTests(IsolatedAsyncioTestCase):
    setUp = fixture.ResumeGenerationTests.setUp
    prepare = fixture.ResumeGenerationTests.prepare
    generate = fixture.ResumeGenerationTests.generate

    async def test_private_routes_reject_missing_and_wrong_credentials_before_parsing(self):
        protected = APIProtection(app, TOKEN, settings.FRONTEND_ORIGINS)
        self.client = AsyncClient(transport=ASGITransport(app=protected), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)
        record_id = uuid4()
        routes = [
            ("GET", "/api/access"), ("GET", "/api/matches/sources"), ("GET", f"/api/matches/{record_id}"),
            ("GET", f"/api/matches/{record_id}/editor"), ("PUT", f"/api/matches/{record_id}/skills"),
            ("PUT", f"/api/matches/{record_id}/suggestions/{uuid4()}/edit"),
            ("POST", f"/api/matches/{record_id}/optimize"),
            ("PATCH", f"/api/matches/{record_id}/suggestions/{uuid4()}"),
            ("POST", "/api/matches"), ("POST", "/api/jobs/analyze"),
            ("POST", "/api/resumes/upload"), ("GET", f"/api/resumes/{record_id}/extract"),
            ("GET", f"/api/resumes/{record_id}/original-pdf?match_id={uuid4()}"),
            ("POST", "/api/resumes/generate"), ("POST", "/api/resumes/generate/compatibility"),
            ("GET", f"/api/resumes/generated/{record_id}"),
            ("GET", f"/api/resumes/generated/{record_id}/download?inline=true"),
        ]
        for method, route in routes:
            with self.subTest(route=route):
                result = await self.client.request(method, route, headers={"Authorization": "Bearer wrong"})
                self.assertEqual(result.status_code, 401)
                self.assertEqual(result.headers["cache-control"], "no-store")
                self.assertEqual(result.headers["www-authenticate"], "Bearer")
        self.assertEqual((await self.client.get("/api/access")).status_code, 401)
        self.client.headers["Authorization"] = f"Bearer {TOKEN}"
        self.assertEqual((await self.client.get("/api/access")).status_code, 200)
        preload = await self.client.get("/api/matches/sources")
        self.assertEqual(preload.status_code, 200)
        generation = await self.generate(await self.prepare(fixture.sample_resume()))
        download = f'/api/resumes/generated/{generation["generation_id"]}/download'
        self.assertEqual((await self.client.get(download)).status_code, 200)
        self.client.headers.pop("Authorization")
        self.assertEqual((await self.client.get(download)).status_code, 401)

    async def test_untrusted_origins_and_rebinding_hosts_are_rejected(self):
        hostile_origin = {"Origin": "https://untrusted.example.test"}
        response = await self.client.post("/api/jobs/analyze", headers=hostile_origin, json={"job_description": "Python"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("access-control-allow-origin", response.headers)
        for origin, expected in (("http://localhost:5173", 200), (hostile_origin["Origin"], 400)):
            with self.subTest(origin=origin):
                preflight = await self.client.options("/api/resumes/upload", headers={
                    "Origin": origin, "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization",
                })
                self.assertEqual(preflight.status_code, expected)
        self.assertEqual((await self.client.get("/api/matches/sources", headers={"Host": "other.example.test"})).status_code, 400)
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("192.0.2.1", 12345)), base_url="http://127.0.0.1",
        ) as remote:
            self.assertEqual((await remote.get("/api/matches/sources")).status_code, 403)

    async def test_active_pdf_and_unsafe_docx_parts_leave_no_uploads(self):
        buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        writer.add_attachment("synthetic.txt", b"synthetic attachment")
        writer.write(buffer)
        self.assertEqual((await self.client.post("/api/resumes/upload", files={
            "file": ("attachment.pdf", buffer.getvalue()),
        })).status_code, 400)

        for name, content in (
            ("word/styles.xml", "<!DOCTYPE styles><styles/>"),
            ("word/embeddings/inert.bin", b"synthetic embedded object"),
        ):
            with self.subTest(name=name):
                buffer = BytesIO(sample_docx())
                with ZipFile(buffer, "a") as archive:
                    archive.writestr(name, content)
                response = await self.client.post("/api/resumes/upload", files={"file": ("sample.docx", buffer.getvalue())})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(list(self.uploads.iterdir()), [])

    async def test_resume_with_phone_and_incomplete_web_links_can_be_uploaded_for_ats(self):
        writer = PdfWriter(clone_from=BytesIO(synthetic_pdf([(80, "Skills"), (110, "Python")])))
        for index, uri in enumerate(("tel:+15550100100", "example.test/portfolio", "https://example.test/my portfolio")):
            top = 120 + index * 20
            writer.add_annotation(0, Link(rect=(25, top, 90, top + 12), url=uri))
        buffer = BytesIO()
        writer.write(buffer)
        original = buffer.getvalue()
        request = await self.prepare(original, "contact-links.pdf")
        result = await self.generate(request)
        generation_id = result["generation_id"]
        download = await self.client.get(f"/api/resumes/generated/{generation_id}/download")
        self.assertEqual(download.status_code, 200)
        with pymupdf.open(stream=download.content, filetype="pdf") as exported:
            self.assertIn("Python", exported[0].get_text())
            self.assertEqual(exported[0].get_links(), [])

        resume_id, match_id = request["resume_id"], request["match_id"]
        self.assertEqual((self.uploads / f"{resume_id}.pdf").read_bytes(), original)
        preview = await self.client.get(f"/api/resumes/{resume_id}/original-pdf", params={"match_id": match_id})
        self.assertEqual(preview.status_code, 422)
        self.assertIn("original-layout preview", preview.json()["detail"])
        layout = await self.client.post("/api/resumes/generate", json={**request, "mode": "original_layout"})
        self.assertEqual(layout.status_code, 422)
        self.assertIn("ATS-Friendly PDF", layout.json()["detail"]["message"])

    async def test_pdf_with_local_file_link_is_still_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        writer.add_annotation(0, Link(rect=(25, 80, 90, 95), url="file:///example/document.pdf"))
        buffer = BytesIO()
        writer.write(buffer)
        upload = await self.client.post("/api/resumes/upload", files={"file": ("local-link.pdf", buffer.getvalue())})
        self.assertEqual(upload.status_code, 400)
        self.assertEqual(list(self.uploads.iterdir()), [])

    async def test_uuid_path_and_filename_validation(self):
        uploaded = await self.client.post("/api/resumes/upload", files={"file": ("folder\\resume.pdf", sample_pdf())})
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(uploaded.json()["original_filename"], "resume.pdf")
        resume_id = uploaded.json()["resume_id"]
        self.assertEqual({path.name for path in self.uploads.iterdir()}, {f"{resume_id}.pdf", f"{resume_id}.name.json"})
        for path in (
            "/api/resumes/bad-uuid/extract", "/api/matches/bad-uuid",
            "/api/resumes/generated/bad-uuid/download",
        ):
            self.assertEqual((await self.client.get(path)).status_code, 422)
        self.assertEqual((await self.client.get(f"/api/resumes/{uuid4()}/extract")).status_code, 404)

    async def test_json_limits_and_errors_do_not_echo_private_input(self):
        private = "synthetic-confidential-content"
        result = await self.client.post("/api/jobs/analyze", json={"job_description": {private: private}, private: private})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn(private, result.text)
        async def body():
            for _ in range(3):
                yield b"a" * (256 * 1024)
        result = await self.client.post("/api/jobs/analyze", content=body(), headers={"Content-Type": "application/json"})
        self.assertEqual(result.status_code, 413)
        with patch("app.job_routes.analyze_job_description", side_effect=RuntimeError(private)), self.assertLogs(
            "app.api_protection", level="ERROR",
        ) as logs:
            result = await self.client.post("/api/jobs/analyze", json={"job_description": "Python"})
        self.assertEqual(result.status_code, 500)
        self.assertNotIn(private, result.text + " ".join(logs.output))

    async def test_approval_rechecks_saved_candidate_against_source(self):
        request = await self.prepare(fixture.sample_resume())
        record = match_storage.load_match(UUID(request["match_id"]))
        candidate = record.candidates[0]
        candidate.suggested_replacement = "Invented a professional certification."
        suggestion = Suggestion(suggestion_id=uuid4(), **candidate.model_dump(exclude={"candidate_id"}))
        record.report.optimization = OptimizationResult(status="completed", suggestions=[suggestion])
        match_storage.save_match(record, overwrite=True)
        path = self.matches / f'{request["match_id"]}.json'
        before = path.read_bytes()
        result = await self.client.patch(
            f'/api/matches/{request["match_id"]}/suggestions/{suggestion.suggestion_id}',
            json={"approval_status": "accepted"},
        )
        self.assertEqual(result.status_code, 409)
        self.assertEqual(path.read_bytes(), before)
