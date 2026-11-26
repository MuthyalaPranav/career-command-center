from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter

from app.main import app
from app.resume_validation import DOCX_TYPE, MAX_RESUME_SIZE, PDF_TYPE


def sample_pdf() -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)
    return buffer.getvalue()


def sample_docx(document_xml: str | None = None) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="word/document.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
            '</Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            document_xml if document_xml is not None else (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p/></w:body></w:document>'
            ),
        )
    return buffer.getvalue()


class ResumeUploadTests(IsolatedAsyncioTestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        self.directory = Path(resources.enter_context(TemporaryDirectory()))
        resources.enter_context(patch("app.resume_routes.UPLOAD_DIRECTORY", self.directory))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)

    async def upload(self, filename, content, mime_type="application/octet-stream"):
        return await self.client.post("/api/resumes/upload", files={"file": (filename, content, mime_type)})

    def assert_stored_resume(self, response, filename, suffix, content, mime_type):
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        resume_id = UUID(data["resume_id"])
        self.assertEqual(resume_id.version, 4)
        self.assertEqual(data, {
            "resume_id": str(resume_id),
            "original_filename": filename,
            "file_type": mime_type,
            "file_size": len(content),
            "message": "Resume uploaded successfully.",
        })
        stored_path = self.directory / f"{resume_id}{suffix}"
        self.assertEqual(stored_path.read_bytes(), content)
        self.assertEqual(set(self.directory.iterdir()), {stored_path, self.directory / f"{resume_id}.name.json"})

    async def test_accepts_valid_pdf(self):
        content = sample_pdf()
        response = await self.upload("Resume.PDF", content, "text/plain")
        self.assert_stored_resume(response, "Resume.PDF", ".pdf", content, PDF_TYPE)

    async def test_accepts_valid_docx(self):
        content = sample_docx()
        response = await self.upload("Resume.docx", content)
        self.assert_stored_resume(response, "Resume.docx", ".docx", content, DOCX_TYPE)

    async def test_rejects_unsupported_extension_and_mismatched_signature(self):
        for filename, content in (("resume.txt", sample_pdf()), ("resume.pdf", b"plain text"),
                                  ("resume.docx", sample_pdf())):
            with self.subTest(filename=filename):
                response = await self.upload(filename, content, "application/pdf")
                self.assertEqual(response.status_code, 415, response.text)
                self.assertEqual(list(self.directory.iterdir()), [])

    async def test_rejects_invalid_and_truncated_pdf(self):
        for content in (b"%PDF-1.7\ninvalid\n%%EOF\n", sample_pdf()[:-4]):
            with self.subTest(content=content[-12:]):
                response = await self.upload("resume.pdf", content)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(list(self.directory.iterdir()), [])

    async def test_rejects_corrupted_docx_and_ordinary_zip(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("notes.txt", "Plain ZIP archive")
        invalid_encoding = sample_docx('<?xml version="1.0" encoding="unknown-encoding"?><w/>')
        for content in (sample_docx()[:-10], sample_docx("<invalid>"), invalid_encoding, buffer.getvalue()):
            with self.subTest(content=content[-12:]):
                response = await self.upload("resume.docx", content)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(list(self.directory.iterdir()), [])

    async def test_rejects_empty_file(self):
        response = await self.upload("resume.pdf", b"")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("empty", response.json()["detail"])
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_rejects_oversized_file_and_cleans_up(self):
        content = b"%PDF-1.7\n".ljust(MAX_RESUME_SIZE + 1, b"x")
        response = await self.upload("large.pdf", content)
        self.assertEqual(response.status_code, 413, response.text)
        self.assertIn("5 MB", response.json()["detail"])
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_caps_multipart_stream_without_content_length(self):
        content = b"x" * (MAX_RESUME_SIZE + 128 * 1024)
        multipart = self.client.build_request(
            "POST", "/api/resumes/upload", files={"file": ("large.pdf", content)},
        )
        async def chunks():
            async for chunk in multipart.stream:
                yield chunk

        response = await self.client.post(
            "/api/resumes/upload",
            headers={"Content-Type": multipart.headers["Content-Type"]},
            content=chunks(),
        )
        self.assertNotIn("Content-Length", response.request.headers)
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_rejects_multiple_files(self):
        response = await self.client.post("/api/resumes/upload", files=[
            ("file", ("one.pdf", sample_pdf())),
            ("file", ("two.pdf", sample_pdf())),
        ])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_rejects_malformed_multipart(self):
        response = await self.client.post(
            "/api/resumes/upload",
            headers={"Content-Type": "multipart/form-data; boundary=expected"},
            content=b"--wrong\r\ninvalid data\r\n",
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_upload_cors_allows_local_frontend(self):
        origin = "http://localhost:5173"
        response = await self.client.options("/api/resumes/upload", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
