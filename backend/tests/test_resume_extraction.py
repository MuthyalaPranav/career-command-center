import json
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from uuid import uuid4

import pymupdf
from docx import Document
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.resume_sections import ResumeLine, parse_sections
from app.resume_validation import DOCX_TYPE, PDF_TYPE


def sample_pdf(*page_types: str) -> bytes:
    with pymupdf.open() as document:
        for kind in page_types:
            page = document.new_page(width=612, height=792)
            if kind == "text":
                # Paint the blocks out of order so the test checks visual sorting.
                page.insert_text((60, 162), "Python and FastAPI", fontsize=10)
                page.insert_text((60, 140), "Technical Skills", fontsize=12)
                page.insert_text((60, 116), "Builds reliable APIs.", fontsize=10)
                page.insert_text((60, 94), "Summary", fontsize=12)
            elif kind == "image":
                pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0)
                pixmap.clear_with(180)
                page.insert_image(pymupdf.Rect(45, 45, 566, 746), pixmap=pixmap)
        return document.tobytes()


def sample_docx(*, empty: bool = False) -> bytes:
    document = Document()
    if not empty:
        document.add_paragraph("alex@example.test")
        document.add_heading("Work Experience", level=1)
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).merge(table.cell(1, 0)).text = "Example Studio"
        table.cell(0, 1).text = "Developer"
        cell = table.cell(1, 1)
        cell.text = "Built APIs"
        cell.add_table(rows=1, cols=1).cell(0, 0).text = "Maintained services"
        cell.add_paragraph("Reviewed releases")
        document.add_heading("Education", level=2)
        document.add_paragraph("Example University")
        document.add_heading("Community Work", level=1)
        document.add_paragraph("Organized a café workshop")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class ResumeSectionTests(TestCase):
    def test_recognizes_seven_sections_and_preserves_unclassified_text(self):
        lines = [
            ResumeLine("Alex Example"),
            ResumeLine("alex@example.test"),
            ResumeLine("+1 (212) 555-0100"),
            ResumeLine("Professional Summary: Builds tools."),
            ResumeLine("TECHNICAL SKILLS"),
            ResumeLine("Python"),
            ResumeLine("Professional Experience"),
            ResumeLine("Developer at Example Studio"),
            ResumeLine("Selected Projects"),
            ResumeLine("Portfolio"),
            ResumeLine("Education"),
            ResumeLine("Example University"),
            ResumeLine("Certifications & Licenses"),
            ResumeLine("Example Certificate"),
            ResumeLine("Volunteering"),
            ResumeLine("Mentored peers"),
            ResumeLine("Additional Activities:"),
            ResumeLine("Organized a meetup"),
            ResumeLine("Skills: React"),
        ]
        self.assertEqual(parse_sections(lines), {
            "contact_information": ["alex@example.test", "+1 (212) 555-0100"],
            "professional_summary": ["Builds tools."],
            "technical_skills": ["Python", "React"],
            "work_experience": ["Developer at Example Studio"],
            "projects": ["Portfolio"],
            "education": ["Example University"],
            "certifications": ["Example Certificate"],
            "unclassified": ["Alex Example", "Volunteering", "Mentored peers", "Additional Activities:", "Organized a meetup"],
        })

    def test_does_not_fill_missing_information(self):
        sections = parse_sections([ResumeLine("Unlabelled source text")])
        self.assertEqual(sections.pop("unclassified"), ["Unlabelled source text"])
        self.assertTrue(all(not entries for entries in sections.values()))


class ResumeExtractionTests(IsolatedAsyncioTestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        root = Path(resources.enter_context(TemporaryDirectory()))
        self.directory = root / "uploads"
        self.directory.mkdir()
        self.extraction_directory = root / "extractions"
        resources.enter_context(patch("app.resume_routes.UPLOAD_DIRECTORY", self.directory))
        resources.enter_context(patch("app.resume_routes.EXTRACTION_DIRECTORY", self.extraction_directory))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)

    async def upload(self, name, content):
        response = await self.client.post("/api/resumes/upload", files={"file": (name, content)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["resume_id"]

    async def extract(self, resume_id):
        return await self.client.get(f"/api/resumes/{resume_id}/extract")

    def assert_saved_json_matches(self, resume_id, response):
        path = self.extraction_directory / f"{resume_id}.json"
        contents = path.read_text(encoding="utf-8")
        self.assertIn('\n  "raw_text": ', contents)
        self.assertEqual(json.loads(contents), response.json())
        return contents

    async def test_pdf_extraction_preserves_visual_order_and_layout(self):
        resume_id = await self.upload("example.pdf", sample_pdf("text", "blank"))
        response = await self.extract(resume_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = response.json()
        self.assertEqual(data["resume_id"], resume_id)
        self.assertEqual(data["file_type"], PDF_TYPE)
        self.assertEqual(data["raw_text"], "Summary\nBuilds reliable APIs.\nTechnical Skills\nPython and FastAPI")
        self.assertEqual(data["sections"]["professional_summary"], ["Builds reliable APIs."])
        self.assertEqual(data["sections"]["technical_skills"], ["Python and FastAPI"])
        self.assertEqual(data["extraction_metadata"]["page_count"], 2)
        self.assertEqual(data["extraction_metadata"]["image_only_pages"], [])

        layout = data["layout_metadata"]
        self.assertEqual(layout["coordinate_system"], "unrotated_pymupdf_points")
        self.assertEqual([page["page_number"] for page in layout["pages"]], [1, 2])
        first, second = layout["pages"]
        self.assertEqual(first["crop_box"], [0, 0, 612, 792])
        self.assertEqual(len(first["pdf_to_page_transform"]), 6)
        block = first["blocks"][0]
        self.assertEqual(len(block["bbox"]), 4)
        span = block["lines"][0]["spans"][0]
        self.assertEqual(span["text"], "Summary")
        self.assertEqual(span["origin"], [60, 94])
        self.assertEqual(span["font"], "Helvetica")
        self.assertEqual(span["font_size"], 12)
        self.assertEqual(len(span["bbox"]), 4)
        self.assertEqual(second["blocks"], [])
        self.assert_saved_json_matches(resume_id, response)
        private_file = await self.client.get(f"/private_extractions/{resume_id}.json")
        self.assertEqual(private_file.status_code, 404)

    async def test_docx_paragraph_table_and_heading_metadata(self):
        resume_id = await self.upload("example.docx", sample_docx())
        response = await self.extract(resume_id)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["resume_id"], resume_id)
        self.assertEqual(data["file_type"], DOCX_TYPE)
        self.assertNotIn("layout_metadata", data)
        self.assertEqual([line for line in data["raw_text"].splitlines() if line], [
            "alex@example.test", "Work Experience", "Example Studio", "Developer", "Built APIs",
            "Maintained services", "Reviewed releases", "Education", "Example University",
            "Community Work", "Organized a café workshop",
        ])
        self.assertEqual(data["sections"]["work_experience"], [
            "Example Studio", "Developer", "Built APIs", "Maintained services", "Reviewed releases",
        ])
        self.assertEqual(data["sections"]["unclassified"], ["Community Work", "Organized a café workshop"])
        metadata = data["extraction_metadata"]
        self.assertEqual(metadata["table_count"], 2)
        paragraphs = {paragraph["text"]: paragraph for paragraph in metadata["paragraphs"]}
        self.assertEqual(paragraphs["Education"]["style"], "Heading 2")
        self.assertEqual(paragraphs["Education"]["heading_level"], 2)
        self.assertEqual(paragraphs["Maintained services"]["table_path"], [
            {"table": 1, "row": 2, "column": 2}, {"table": 2, "row": 1, "column": 1},
        ])
        self.assertIn("café", self.assert_saved_json_matches(resume_id, response))

    async def test_rejects_missing_and_invalid_resume_ids(self):
        missing = await self.extract(uuid4())
        invalid = await self.extract("not-a-uuid.pdf")
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(invalid.status_code, 422, invalid.text)

    async def test_rejects_empty_documents_and_image_only_pdfs(self):
        for filename, content, expected in (
            ("empty.pdf", sample_pdf("blank"), "no readable text"),
            ("scanned.pdf", sample_pdf("image"), "OCR is not yet supported"),
            ("empty.docx", sample_docx(empty=True), "no readable text"),
        ):
            with self.subTest(filename=filename):
                resume_id = await self.upload(filename, content)
                response = await self.extract(resume_id)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn(expected, response.json()["detail"])
        self.assertFalse(self.extraction_directory.exists())

    async def test_mixed_pdf_reports_image_only_page_numbers(self):
        resume_id = await self.upload("mixed.pdf", sample_pdf("image", "text"))
        response = await self.extract(resume_id)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["extraction_metadata"]["image_only_pages"], [1])
        self.assertIn("Python and FastAPI", data["raw_text"])

    async def test_reports_stored_file_that_cannot_be_parsed(self):
        for suffix in ("pdf", "docx"):
            with self.subTest(suffix=suffix):
                resume_id = uuid4()
                (self.directory / f"{resume_id}.{suffix}").write_bytes(b"damaged source")
                response = await self.extract(resume_id)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn(str(self.directory), response.text)

    async def test_original_files_are_unchanged_after_repeated_extraction(self):
        for name, content in (("original.pdf", sample_pdf("text")), ("original.docx", sample_docx())):
            with self.subTest(name=name):
                resume_id = await self.upload(name, content)
                path = self.directory / f"{resume_id}{Path(name).suffix}"
                before = path.stat().st_mtime_ns
                files_before_extraction = set(self.directory.iterdir())
                first = await self.extract(resume_id)
                with patch("app.resume_routes.extract_resume", side_effect=AssertionError("Should use the saved snapshot")):
                    second = await self.extract(resume_id)
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(first.json(), second.json())
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(path.stat().st_mtime_ns, before)
                self.assertEqual(set(self.directory.iterdir()), files_before_extraction)

    async def test_repeated_extraction_replaces_previous_json(self):
        resume_id = await self.upload("example.pdf", sample_pdf("text"))
        first = await self.extract(resume_id)
        self.assertEqual(first.status_code, 200, first.text)
        saved = self.extraction_directory / f"{resume_id}.json"
        saved.write_text('{"stale": true}', encoding="utf-8")

        second = await self.extract(resume_id)

        self.assertEqual(second.status_code, 200, second.text)
        self.assert_saved_json_matches(resume_id, second)
        self.assertEqual(list(self.extraction_directory.iterdir()), [saved])

    async def test_save_failure_reports_error_and_preserves_previous_json(self):
        resume_id = await self.upload("example.pdf", sample_pdf("text"))
        first = await self.extract(resume_id)
        self.assertEqual(first.status_code, 200, first.text)
        saved = self.extraction_directory / f"{resume_id}.json"
        saved.write_text('{"stale": true}', encoding="utf-8")
        previous = saved.read_bytes()

        with (
            self.assertLogs("app.resume_routes", level="ERROR"),
            patch("app.resume_storage.Path.replace", side_effect=PermissionError(str(saved))),
        ):
            response = await self.extract(resume_id)

        self.assertEqual(response.status_code, 500, response.text)
        self.assertIn("JSON could not be saved", response.json()["detail"])
        self.assertNotIn("private_extractions", response.text)
        self.assertNotIn(str(self.extraction_directory), response.text)
        self.assertEqual(saved.read_bytes(), previous)
        self.assertEqual(list(self.extraction_directory.iterdir()), [saved])
