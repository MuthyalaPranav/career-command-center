import asyncio
import json
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID, uuid4

import pymupdf
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from httpx import ASGITransport, AsyncClient

from app import match_storage
from app.main import app
from app.match_models import GeminiOutput, Suggestion

JOB = "Job title: Developer\nRequired skills: Python, JavaScript, PostgreSQL, HTML"


def sample_resume(*, long=False, minimal=False) -> bytes:
    document = Document()
    document.add_paragraph("Zoë Álvarez")
    document.add_paragraph("zoe@example.test | linkedin.com/in/zoe | https://github.com/zoe")
    document.add_heading("Skills", 1)
    document.add_paragraph("JS, Postgres, Python")
    if not minimal:
        document.add_heading("Projects", 1)
        document.add_paragraph("• Utilized HTML5 in order to build forms for 東京.")
        document.add_paragraph("Record: " + "x" * 420 + " complete")
        paragraph = document.add_paragraph("Source: ")
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), paragraph.part.relate_to("https://github.com/zoe/forms", RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
        run = OxmlElement("w:r")
        label = OxmlElement("w:t")
        label.text = "Project repository"
        run.append(label)
        link.append(run)
        paragraph._p.append(link)
        document.add_heading("Experience", 1)
        document.add_paragraph("January 2022 to September 2024")
        document.add_paragraph("Utilized Python in order to automate reports (2022–2024).", style="List Bullet")
        document.add_heading("Summary", 1)
        document.add_paragraph("Utilized JS to develop interfaces.")
        document.add_heading("Education", 1)
        document.add_paragraph("BSc & diploma <with honors>")
        document.add_heading("Certifications", 1)
        document.add_paragraph("Python certificate")
        document.add_heading("Volunteering", 1)
        document.add_paragraph("Community tutor")
    if long:
        for index in range(190):
            document.add_paragraph(f"Additional entry {index:03} with sufficiently long content for pagination.")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class ResumeGenerationTests(IsolatedAsyncioTestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(TemporaryDirectory()))
        self.uploads = root / "uploads"
        self.extractions = root / "extractions"
        self.matches = root / "matches"
        self.jobs = root / "jobs"
        self.generated = root / "generated"
        for module, values in {
            "app.resume_routes": dict(UPLOAD_DIRECTORY=self.uploads, EXTRACTION_DIRECTORY=self.extractions),
            "app.generation_routes": dict(UPLOAD_DIRECTORY=self.uploads),
            "app.match_storage": dict(EXTRACTION_DIRECTORY=self.extractions, MATCH_DIRECTORY=self.matches,
                                      JOB_ANALYSIS_DIRECTORY=self.jobs),
            "app.job_routes": dict(JOB_ANALYSIS_DIRECTORY=self.jobs),
            "app.generation_storage": dict(GENERATED_DIRECTORY=self.generated),
        }.items():
            stack.enter_context(patch.multiple(module, **values))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)

    async def prepare(self, content, filename="synthetic.docx"):
        upload = await self.client.post("/api/resumes/upload", files={"file": (filename, content)})
        self.assertEqual(upload.status_code, 201, upload.text)
        resume_id = upload.json()["resume_id"]
        extracted = await self.client.get(f"/api/resumes/{resume_id}/extract")
        self.assertEqual(extracted.status_code, 200, extracted.text)
        job = await self.client.post("/api/jobs/analyze", json={"job_description": JOB})
        self.assertEqual(job.status_code, 200, job.text)
        match = await self.client.post("/api/matches", json={"resume_id": resume_id, "job_id": job.json()["job_id"]})
        self.assertEqual(match.status_code, 200, match.text)
        return {"resume_id": resume_id, "match_id": match.json()["match_id"]}

    async def generate(self, request):
        result = await self.client.post("/api/resumes/generate", json=request)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertNotIn("private_generated", result.text)
        return result.json()

    async def pdf(self, metadata):
        base = f'/api/resumes/generated/{metadata["generation_id"]}'
        result = await self.client.get(base)
        self.assertEqual(result.json(), metadata)
        download = await self.client.get(base + "/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["content-type"], "application/pdf")
        self.assertEqual(download.headers["cache-control"], "no-store")
        self.assertTrue(download.headers["content-disposition"].startswith("attachment;"))
        inline = await self.client.get(base + "/download?inline=true")
        self.assertTrue(inline.headers["content-disposition"].startswith("inline;"))
        self.assertEqual(inline.content, download.content)
        return pymupdf.open(stream=download.content, filetype="pdf")

    async def test_unchanged_searchable_unicode_resume_in_requested_order(self):
        request = await self.prepare(sample_resume())
        generation = await self.generate(request)
        self.assertEqual(generation["applied_suggestion_count"], 0)
        self.assertEqual(generation["page_count"], 1)
        with await self.pdf(generation) as document:
            page = document[0]
            text = page.get_text()
            self.assertTrue(text.startswith("Zoë Álvarez"))
            for exact in ("JS, Postgres, Python", "東京", "2022–2024", "Jan 2022 – Sep 2024",
                          "BSc & diploma <with honors>", "Community tutor", "•", "Project repository"):
                self.assertIn(exact, text)
            self.assertEqual(text.count("•"), 2)
            self.assertTrue(page.search_for("Certifications"))
            self.assertIn("x" * 420, "".join(text.split()))
            heads = ["Professional Summary", "Technical Skills", "Work Experience", "Projects", "Education",
                     "Certifications", "Additional Information"]
            self.assertEqual(sorted(heads, key=text.index), heads)
            self.assertEqual({item["uri"] for item in page.get_links()}, {
                "mailto:zoe@example.test", "https://linkedin.com/in/zoe", "https://github.com/zoe",
                "https://github.com/zoe/forms",
            })

    async def test_only_approved_supported_edits_apply_and_sources_stay_identical(self):
        request = await self.prepare(sample_resume())
        match_id = request["match_id"]
        match_url = f"/api/matches/{match_id}"

        def mock_gemini(data):
            return GeminiOutput(suggestions=[{
                "candidate_id": item["candidate_id"], "suggested_replacement": item["suggested_replacement"],
            } for item in data["candidates"]])

        with patch("app.resume_optimization.request_gemini", side_effect=mock_gemini):
            optimized = await self.client.post(match_url + "/optimize")
        self.assertEqual(optimized.status_code, 200, optimized.text)
        suggestions = {item["section"]: item for item in optimized.json()["optimization"]["suggestions"]}
        for section, status in (("technical_skills", "accepted"), ("work_experience", "rejected")):
            suggestion_id = suggestions[section]["suggestion_id"]
            review = await self.client.patch(match_url + f"/suggestions/{suggestion_id}", json={"approval_status": status})
            self.assertEqual(review.status_code, 200, review.text)

        # Simulate an unsupported historical edit marked accepted in private storage.
        record = match_storage.load_match(UUID(match_id))
        project = next(item for item in record.candidates if item.section == "projects")
        project.suggested_replacement = "Invented a new framework."
        record.report.optimization.suggestions.append(Suggestion(
            suggestion_id=uuid4(), approval_status="accepted", **project.model_dump(exclude={"candidate_id"}),
        ))
        match_storage.save_match(record, overwrite=True)
        resume_id = request["resume_id"]
        paths = [
            self.uploads / f"{resume_id}.docx", self.extractions / f"{resume_id}.json",
            self.matches / f"{match_id}.json", self.jobs / f"{record.report.job_id}.json",
        ]
        before = [path.read_bytes() for path in paths]
        generation = await self.generate(request)
        self.assertEqual((generation["approved_suggestion_count"], generation["applied_suggestion_count"],
                          generation["skipped_suggestion_count"]), (2, 1, 1))
        with await self.pdf(generation) as document:
            text = document[0].get_text()
            self.assertIn("JavaScript, PostgreSQL, Python", text)
            self.assertIn("Utilized JS to develop interfaces.", text)
            self.assertIn("Utilized Python in order to automate reports", text)
            self.assertIn("Utilized HTML5 in order to build forms", text)
            self.assertNotIn("Invented a new framework.", text)
        self.assertEqual([path.read_bytes() for path in paths], before)

    async def test_multipage_resume_preserves_all_entries(self):
        generation = await self.generate(await self.prepare(sample_resume(long=True)))
        self.assertGreater(generation["page_count"], 2)
        with await self.pdf(generation) as document:
            self.assertEqual(document.page_count, generation["page_count"])
            pages = [page.get_text() for page in document]
            self.assertTrue(all(page.strip() for page in pages))
            for index in range(190):
                self.assertIn(f"Additional entry {index:03}", " ".join(" ".join(pages).split()))

    async def test_pdf_source_hidden_contact_link_is_recoverable(self):
        destination = "https://www.linkedin.com/in/synthetic-user"
        with pymupdf.open() as source:
            page = source.new_page()
            page.insert_text((72, 72), "Casey Sample\nLinkedIn profile\nSkills\nPython")
            box = page.search_for("LinkedIn profile")[0]
            page.insert_link({"kind": pymupdf.LINK_URI, "from": box, "uri": destination})
            original = source.tobytes()
        request = await self.prepare(original, "synthetic.pdf")
        with await self.pdf(await self.generate(request)) as document:
            self.assertIn("LinkedIn profile", document[0].get_text())
            self.assertTrue(document[0].search_for("Casey Sample"))
            self.assertEqual({link["uri"] for link in document[0].get_links()}, {destination})
        self.assertEqual((self.uploads / f'{request["resume_id"]}.pdf').read_bytes(), original)

    async def test_minimal_resume_and_concurrent_repeated_exports(self):
        request = await self.prepare(sample_resume(minimal=True))
        first, second = await asyncio.gather(self.generate(request), self.generate(request))
        self.assertNotEqual(first["generation_id"], second["generation_id"])
        for generation in (first, second):
            with await self.pdf(generation) as document:
                self.assertEqual(document.page_count, 1)
                self.assertIn("Technical Skills", document[0].get_text())
                self.assertNotIn("Work Experience", document[0].get_text())
            folder = self.generated / generation["generation_id"]
            self.assertEqual({path.name for path in folder.iterdir()}, {"resume.pdf", f'{generation["generation_id"]}.json'})

    async def test_invalid_missing_mismatched_and_incomplete_records(self):
        request = await self.prepare(sample_resume(minimal=True))
        for payload, expected in (
            ({**request, "resume_id": "../private_uploads"}, 422),
            ({**request, "match_id": "invalid"}, 422),
            ({**request, "match_id": str(uuid4())}, 404),
            ({**request, "resume_id": str(uuid4())}, 422),
        ):
            result = await self.client.post("/api/resumes/generate", json=payload)
            self.assertEqual(result.status_code, expected, result.text)
        generation = await self.generate(request)
        folder = self.generated / generation["generation_id"]
        (folder / "resume.pdf").unlink()
        for suffix in ("", "/download"):
            result = await self.client.get(f'/api/resumes/generated/{generation["generation_id"]}{suffix}')
            self.assertEqual(result.status_code, 404)
        result = await self.client.get("/api/resumes/generated/%2e%2e%2fprivate_uploads/download")
        self.assertIn(result.status_code, {404, 422})
        (self.extractions / f'{request["resume_id"]}.json').unlink()
        result = await self.client.post("/api/resumes/generate", json=request)
        self.assertEqual(result.status_code, 404)

    async def test_failed_validation_and_storage_leave_no_published_export(self):
        request = await self.prepare(sample_resume(minimal=True))
        path = self.extractions / f'{request["resume_id"]}.json'
        snapshot = json.loads(path.read_bytes())
        path.write_text(json.dumps({**snapshot, "raw_text": "", "sections": {}}), encoding="utf-8")
        result = await self.client.post("/api/resumes/generate", json=request)
        self.assertEqual(result.status_code, 422, result.text)
        self.assertFalse(self.generated.exists())

        path.write_text(json.dumps(snapshot), encoding="utf-8")
        with patch("app.generation_storage.write_private_json", side_effect=OSError("synthetic failure")):
            result = await self.client.post("/api/resumes/generate", json=request)
        self.assertEqual(result.status_code, 500, result.text)
        self.assertEqual(list(self.generated.iterdir()), [])
