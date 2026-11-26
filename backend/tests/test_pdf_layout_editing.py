import json
from uuid import UUID, uuid4

import pymupdf

import test_resume_generation as fixture
from app import match_storage
from app.match_models import GeminiOutput, Suggestion


def synthetic_pdf(rows, *, width=612, other_page=False, duplicate=False, missing_font=False, scanned=False):
    with pymupdf.open() as document:
        page = document.new_page(width=width, height=420)
        if scanned:
            pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
            pixel.clear_with(160)
            page.insert_image(pymupdf.Rect(30, 30, 100, 100), pixmap=pixel)
        else:
            page.insert_text((30, 37), "Casey Sample", fontsize=14)
            for y, text in rows:
                page.insert_text((30, y), text, fontsize=11, color=(0.1, 0.25, 0.4))
            if duplicate:
                y, text = rows[-1]
                page.insert_text((30, y), text, fontsize=11, color=(0.1, 0.25, 0.4))
            if missing_font:
                xref = page.get_fonts()[0][0]
                document.xref_set_key(xref, "BaseFont", "/UnrecognizedResumeFace")
        if other_page:
            page = document.new_page(width=510, height=390)
            page.insert_text((40, 45), "Portfolio")
            page.insert_link({"kind": pymupdf.LINK_URI, "uri": "https://example.test/portfolio",
                              "from": page.search_for("Portfolio")[0]})
            page.add_rect_annot(pymupdf.Rect(50, 110, 100, 140)).update()
            page.draw_line((30, 180), (470, 180), color=(0, 0.5, 0), width=2)
        return document.tobytes()


class PDFLayoutTests(fixture.IsolatedAsyncioTestCase):
    setUp = fixture.ResumeGenerationTests.setUp
    prepare = fixture.ResumeGenerationTests.prepare
    pdf = fixture.ResumeGenerationTests.pdf

    async def reviews(self, request, decisions):
        def mock_gemini(data):
            return GeminiOutput(suggestions=[{
                "candidate_id": item["candidate_id"], "suggested_replacement": item["suggested_replacement"],
            } for item in data["candidates"]])

        url = f'/api/matches/{request["match_id"]}'
        with fixture.patch("app.resume_optimization.request_gemini", side_effect=mock_gemini):
            response = await self.client.post(url + "/optimize")
        self.assertEqual(response.status_code, 200, response.text)
        for item in response.json()["optimization"]["suggestions"]:
            decision = decisions.get(item["section"])
            if decision:
                result = await self.client.patch(f'{url}/suggestions/{item["suggestion_id"]}',
                                                 json={"approval_status": decision})
                self.assertEqual(result.status_code, 200, result.text)

    async def check(self, request):
        compatibility = await self.client.post("/api/resumes/generate/compatibility", json=request)
        self.assertEqual(compatibility.status_code, 200, compatibility.text)
        return compatibility.json()

    async def layout_export(self, request, status=201):
        result = await self.client.post("/api/resumes/generate", json={**request, "mode": "original_layout"})
        self.assertEqual(result.status_code, status, result.text)
        return result.json()

    async def test_equal_shorter_and_longer_edits_preserve_page_geometry_and_content(self):
        original = synthetic_pdf([
            (64, "Summary"), (82, "Utilized JS; utilized Python for reporting."),
            (110, "Skills"), (128, "JS"),
            (156, "Experience"), (176, "Utilized Python in order to write reports."),
            (218, "Projects"), (238, "Utilized HTML5 for forms."),
        ], other_page=True)
        request = await self.prepare(original, "source.pdf")
        await self.reviews(request, {
            "professional_summary": "accepted", "technical_skills": "accepted", "work_experience": "accepted",
            "projects": "rejected",
        })
        sources = [self.uploads / f'{request["resume_id"]}.pdf', self.extractions / f'{request["resume_id"]}.json']
        before = [path.read_bytes() for path in sources]
        check = await self.check(request)
        self.assertTrue(check["compatible"], check)
        self.assertEqual(check["validation"]["edited_pages"], [1])
        self.assertFalse(self.generated.exists())
        result = await self.layout_export(request)
        self.assertEqual(result["mode"], "original_layout")
        self.assertEqual(result["applied_suggestion_count"], 3)
        self.assertEqual(result["validation"]["visually_compared_pages"], 2)
        with await self.pdf(result) as edited, pymupdf.open(stream=original, filetype="pdf") as source:
            self.assertEqual([page.rect for page in edited], [page.rect for page in source])
            self.assertEqual(edited[1].get_pixmap().samples, source[1].get_pixmap().samples)
            self.assertEqual(edited[1].get_links()[0]["uri"], "https://example.test/portfolio")
            self.assertEqual(len(list(edited[1].annots())), 1)
            text = edited[0].get_text()
            self.assertIn("Used JavaScript; used Python for reporting.", text)
            self.assertIn("Used Python to write reports.", text)
            self.assertIn("Utilized HTML5 for forms.", text)
            self.assertNotIn("Utilized JS; utilized", text)
            self.assertTrue(edited[0].search_for("JavaScript"))
        self.assertEqual([path.read_bytes() for path in sources], before)
        original_url = f'/api/resumes/{request["resume_id"]}/original-pdf?match_id={request["match_id"]}'
        original_preview = await self.client.get(original_url)
        self.assertEqual(original_preview.content, original)
        self.assertEqual(original_preview.headers["cache-control"], "no-store")
        self.assertTrue(original_preview.headers["content-disposition"].startswith("inline;"))
        mismatch = await self.client.get(f'/api/resumes/{uuid4()}/original-pdf?match_id={request["match_id"]}')
        self.assertEqual(mismatch.status_code, 422)

    async def test_wraps_longer_replacement_in_free_space(self):
        line = "JS and Python for testing deployment"
        width = pymupdf.Font("helv").text_length(line, fontsize=11) + 62
        request = await self.prepare(synthetic_pdf([(63, "Skills"), (94, line)], width=width), "source.pdf")
        await self.reviews(request, {"technical_skills": "accepted"})
        result = await self.layout_export(request)
        with await self.pdf(result) as document:
            self.assertIn("JavaScript and Python for testing\ndeployment", document[0].get_text())

    async def test_overflow_is_reported_without_publishing_and_ats_still_works(self):
        line = "JS and Python for testing deployment"
        width = pymupdf.Font("helv").text_length(line, fontsize=11) + 62
        request = await self.prepare(synthetic_pdf([(63, "Skills"), (94, line), (109, "Contact information")],
                                                   width=width), "source.pdf")
        await self.reviews(request, {"technical_skills": "accepted"})
        result = await self.check(request)
        self.assertFalse(result["compatible"])
        self.assertIn("overlap", result["issues"][0]["reason"])
        failure = await self.layout_export(request, 422)
        self.assertIn("ATS-Friendly", failure["detail"]["message"])
        self.assertFalse(self.generated.exists())
        ats = await self.client.post("/api/resumes/generate", json=request)
        self.assertEqual(ats.status_code, 201, ats.text)
        self.assertEqual(ats.json()["mode"], "ats")

    async def test_pending_is_never_applied_and_zero_edits_copies_source(self):
        original = synthetic_pdf([(62, "Skills"), (86, "JS")])
        request = await self.prepare(original, "source.pdf")
        await self.reviews(request, {})
        result = await self.layout_export(request)
        self.assertEqual(result["applied_suggestion_count"], 0)
        self.assertEqual(result["validation"]["edited_pages"], [])
        download = await self.client.get(f'/api/resumes/generated/{result["generation_id"]}/download')
        self.assertEqual(download.content, original)

    async def test_unsupported_stored_approval_is_reported(self):
        request = await self.prepare(synthetic_pdf([(62, "Skills"), (86, "JS")]), "source.pdf")
        await self.reviews(request, {"technical_skills": "accepted"})
        record = match_storage.load_match(UUID(request["match_id"]))
        item = record.report.optimization.suggestions[0]
        record.report.optimization.suggestions[0] = Suggestion.model_validate({
            **item.model_dump(), "suggested_replacement": "Invented technology",
        })
        match_storage.save_match(record, overwrite=True)
        result = await self.layout_export(request, 422)
        self.assertIn("could not be verified", result["detail"]["issues"][0]["reason"])

    async def test_duplicate_glyphs_and_missing_font_are_unsupported(self):
        for fixture_options, expected in (({"duplicate": True}, "ambiguous"), ({"missing_font": True}, "font")):
            with self.subTest(fixture_options=fixture_options):
                request = await self.prepare(synthetic_pdf([(62, "Skills"), (86, "JS")], **fixture_options), "source.pdf")
                await self.reviews(request, {"technical_skills": "accepted"})
                result = await self.check(request)
                self.assertFalse(result["compatible"], result)
                self.assertIn(expected, str(result["issues"]).lower())

    async def test_identical_text_on_two_pages_is_resolved_by_the_approved_source_position(self):
        with pymupdf.open() as document:
            for _ in range(2):
                page = document.new_page()
                page.insert_text((35, 55), "Skills")
                page.insert_text((35, 75), "JS")
            original = document.tobytes()
        request = await self.prepare(original, "source.pdf")
        await self.reviews(request, {})
        record = match_storage.load_match(UUID(request["match_id"]))
        record.report.optimization.suggestions[1].approval_status = "accepted"
        match_storage.save_match(record, overwrite=True)
        result = await self.layout_export(request)
        self.assertEqual(result["validation"]["edited_pages"], [2])
        with await self.pdf(result) as edited:
            self.assertIn("JS", edited[0].get_text())
            self.assertIn("JavaScript", edited[1].get_text())

    async def test_docx_scanned_corrupt_and_stale_sources(self):
        docx = await self.prepare(fixture.sample_resume(minimal=True))
        result = await self.check(docx)
        self.assertFalse(result["original_preview_available"])
        self.assertIn("DOCX", result["message"])

        request = await self.prepare(synthetic_pdf([(62, "Skills"), (86, "JS")]), "source.pdf")
        original = self.uploads / f'{request["resume_id"]}.pdf'
        path = self.extractions / f'{request["resume_id"]}.json'
        snapshot = json.loads(path.read_bytes())
        snapshot["layout_metadata"]["pages"][0]["blocks"] = []
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        self.assertIn("stale", (await self.check(request))["message"])
        original.write_bytes(synthetic_pdf([], scanned=True))
        self.assertIn("scanned", (await self.check(request))["message"])
        original.write_bytes(b"%PDF-broken")
        result = await self.check(request)
        self.assertFalse(result["original_preview_available"])
        self.assertIn("corrupt", result["message"])
