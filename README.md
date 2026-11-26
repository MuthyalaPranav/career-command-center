# resume-editor-byJD

Career Command Center extracts structured text from PDFs and DOCX files, analyzes job descriptions, and exports ATS-friendly or original-layout PDFs with approved wording edits. A private workspace keeps an active master resume and recommends relevant saved exports for each new job description.

For production configuration, see the [deployment guide](docs/deployment-guide.md). Verification results and release blockers are in the [final test report](docs/final-test-report.md).

## Folder structure

| Path | Purpose |
| --- | --- |
| `frontend/` | React workspace with the dashboard, Master Resume, JD Analyzer, AI Resume Studio, Resume Library, and Resume History |
| `backend/` | FastAPI routes, resume services, JD analysis rules, private storage, and tests |
| `docs/` | Project goals, architecture, and development phases |
| `.env.example` | Reference for local and production environment settings |

## Run locally on Windows

Install Node.js 22.12 or later with npm, and Python 3.13 with the Windows `py` launcher. Open two Command Prompt (CMD) windows from the project root, one per app.

### Frontend

```cmd
cd frontend
npm ci
npm run dev
```

### Backend

```cmd
cd backend
if not exist .venv\Scripts\python.exe py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2"
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Local configuration

The API allows cross-origin GET, POST, PATCH, PUT, and DELETE requests from `http://localhost:5173` by default. To override the origin, use `set "FRONTEND_ORIGINS=http://localhost:5174"` in the backend CMD window before starting Uvicorn and start the frontend with `npm run dev -- --port 5174`. Use the same scheme, hostname, and port in the browser as in the configured origin.

The local commands bind to loopback. If you set `RESUME_WORKSPACE_TOKEN`, unlock the UI with that token and add `-H "Authorization: Bearer %RESUME_WORKSPACE_TOKEN%"` to private API examples. Production requires the token and a private persistent storage path.

The frontend uses `http://127.0.0.1:8000` as its API base URL. To override it, for example when running the API on port 8001, use `set "VITE_API_BASE_URL=http://127.0.0.1:8001"` in the frontend CMD window before starting Vite. See `.env.example` for both settings. Restart an earlier backend process to load new routes.

## Verify the setup

1. Open [http://localhost:5173](http://localhost:5173) and check for the **Your Career Command Center** heading after unlocking the workspace if prompted.
2. Run `curl.exe -i http://127.0.0.1:8000/api/health` in another CMD window. Expect HTTP 200 and `{"status":"ok"}`.
3. Run `curl.exe -i -H "Origin: http://localhost:5173" http://127.0.0.1:8000/api/health`. Check for the `Access-Control-Allow-Origin: http://localhost:5173` response header.
4. Run `npm run build` from `frontend` to type-check and build the production assets.

## Test resume uploads

In **Master Resume**, choose or drop a PDF or DOCX file and check its filename, size, and type. Click **Upload Resume** and look for the success message. Try an unsupported file type, an empty document, or a file larger than 5 MB (5,242,880 bytes) to see the validation messages.

**Settings** also links to the combined tailoring workflow at `#tailor` for the walkthroughs that use one page for upload, analysis, comparison, and export.

Use an unencrypted PDF with 1–100 pages or a standard DOCX. PDFs with attachments or unsupported actions are rejected. DOCX validation limits the expanded archive to 64 MiB and each XML part to 8 MiB, and rejects DTD declarations and embedded active content.

PDFs with phone links, incomplete web links, or web links containing spaces can still be uploaded and extracted for ATS export. An original-layout preview or export may be unavailable for those documents.

To test the API directly, replace `C:\path\to\resume.pdf` with a local PDF or DOCX file.

```cmd
curl.exe -i -F "file=@C:\path\to\resume.pdf" http://127.0.0.1:8000/api/resumes/upload
```

`POST /api/resumes/upload` accepts one multipart field named `file`. A successful response has status `201` and includes a UUID `resume_id`, `original_filename`, the verified MIME type as `file_type`, `file_size` in bytes, and a `message`. The backend saves the document under its UUID in `backend/private_uploads/`, an ignored local directory outside the frontend and API's public routes. Invalid or empty documents return `400`, oversized uploads return `413`, and unsupported extensions or mismatched signatures return `415`.

## Extract and review a resume

1. On [the homepage](http://localhost:5173), upload a text-based PDF or DOCX. For a manual test, use a synthetic resume with headings such as `Summary`, `Skills`, and `Education`.
2. Click **Extract Resume**. Check that content appears under the expected headings and that unassigned text appears under **Unclassified**.
3. Open **View complete raw text** to see the extracted text, including source headings. Try an image-only PDF to check the OCR message.

For a direct API check, copy `resume_id` from the upload response and substitute it below.

```cmd
set "RESUME_ID=replace-with-the-returned-uuid"
curl.exe -i "http://127.0.0.1:8000/api/resumes/%RESUME_ID%/extract"
```

`GET /api/resumes/{resume_id}/extract` uses a UUID to read the stored source. It returns HTTP `200` with `resume_id`, the verified MIME type as `file_type`, `raw_text`, `sections`, and `extraction_metadata`. PDFs also include `layout_metadata`. The API responds with `404` for a missing resume, `422` for an invalid UUID or unreadable document, and `500` if it cannot read the source or save the extracted JSON. Successful extraction responses include `Cache-Control: no-store`.

Each successful extraction saves the full response as UTF-8, indented JSON at `backend/private_extractions/<resume_id>.json`. Repeat requests reuse a valid snapshot. Stale or invalid snapshots are repaired atomically from the original. These directories are Git-ignored and both move under `RESUME_DATA_DIR` when configured.

- **PDF.** PyMuPDF extracts selectable text and orders blocks visually from top to bottom and left to right where possible. Metadata includes 1-based page numbers, page boxes and rotation, the PDF-to-page transform, and text blocks, lines, and spans with bounding boxes, font names, and font sizes. Text geometry uses unrotated PyMuPDF page coordinates in points. Pages with images but no readable text are flagged in a mixed PDF.
- **DOCX.** `python-docx` extracts body paragraphs and table cells in document order. Metadata includes styles, available heading levels, and table cell locations. Merged cells are read once. Raw text places each paragraph on its own line.
- **Sections.** Deterministic rules recognize common English headings and contact details near the top. The API returns explicit empty lists for sections without content and keeps unclassified text. The source file is read-only during extraction.

### Known limits

- OCR for scans is unavailable. A fully image-only PDF returns `422` with an OCR message.
- PDF reading order is approximate for multicolumn layouts, unusual fonts, and overlapping text. Image-only detection operates at the page level, so a scan with a text layer or selectable page number can be reported as a text page.
- DOCX extraction focuses on main-body paragraphs and tables. Headers, footers, text boxes, and tracked revisions may be omitted. Some specialized Word formats are outside `python-docx` support.
- Section classification uses heading and contact patterns. Unmarked headings can be missed, and a line under a recognized heading can stay in that section until another heading is detected.

## Analyze a job description

1. Open [JD Analyzer](http://localhost:5173/#jd-analyzer).
2. Paste a synthetic description with a job title, required and preferred skills, and a negated requirement such as `Docker is not required.` Click **Analyze JD**.
3. Check the listed skills, ATS tags, and other requirements. Expand **View supporting evidence** to see original wording, line numbers, and how each mention was classified.
4. Copy the **Job ID** from the result. In VS Code, press `Ctrl+O` and open `backend/private_job_analyses/<job_id>.json` in the project folder.

`POST /api/jobs/analyze` accepts an object with a nonblank `job_description` string up to 30,000 characters. You can also try it under **jobs** in [Swagger UI](http://127.0.0.1:8000/docs). To send a request from CMD, run:

```cmd
curl.exe -i -H "Content-Type: application/json" --data "{\"job_description\":\"Job title: Backend Developer\nRequired skills: JS\nPreferred skills: Postgres\"}" http://127.0.0.1:8000/api/jobs/analyze
```

HTTP `200` returns `job_id`, `job_title`, `required_skills`, `preferred_skills`, `tools_and_technologies`, `experience_requirements`, `education_requirements`, `responsibilities`, `other_qualifications`, `ats_keywords`, and `evidence`. Missing information appears as `null` for the title or empty arrays for lists. Each evidence item has a `field`, `value`, `snippet`, 1-based `line_number`, a `classification`, and `matched_text` for technical skill mentions. Invalid input returns `422`, and a storage failure returns `500`.

Skill headings and nearby wording classify recognized terms as required, preferred, mentioned, or negated. Negated-only terms stay in evidence. When a term has multiple positive classifications, required takes precedence over preferred. ATS keywords contain one canonical name per recognized positive term. Evidence retains source aliases such as `JS`, `Postgres`, `RESTful API`, and `GenAI`.

Each successful request creates a UUID and saves a private, indented UTF-8 file at `backend/private_job_analyses/<job_id>.json`. The file contains the response fields plus the original `job_description`. Files are published atomically, the directory is Git-ignored, and the API returns the UUID as the reference to the record.

The rule set works best with common English headings and direct wording. Indirect or complex phrasing may be missed, and the dictionary limits the recognized technical keywords. Keyword tags reflect evidence in the JD and are independent of any resume.

## Compare a resume and JD

1. Upload and extract a resume. Paste a JD and click **Analyze JD**. Existing valid extraction and analysis snapshots also work.
2. Scroll to **Resume vs JD matching**, click **Refresh sources**, select the saved records, and click **Compare Resume with JD**.
3. Review **My Resume** and **Job Description** side by side. Expand the green, amber, and red skill tags to inspect matched, partial, and missing evidence. The JD column separates required and preferred skills and lists qualifications and responsibilities.
4. In **Skills Gap Analysis**, choose **Already Know This Skill**, **Not Experienced**, or **Review Later**. To add evidence, choose a source and write one resume-ready sentence describing how you used the skill. The resulting self-reported proposal requires a separate approval.

The comparison deduplicates canonical JD terms in required, preferred, then mentioned order. It checks skills, work experience, and projects using the JD skill dictionary. A confirmed occurrence with the same normalized wording as the JD is exact. A recognized equivalent form is an alias. The comparison retains supporting resume excerpts and JD excerpts, prioritizing the shared wording in an exact match. Summary-only mentions, tentative descriptions, and related terms such as GitHub Actions for CI/CD count as contextual matches. A missing keyword means the comparison found no supporting text. Experience, education, and other qualifications with required or unspecified priority go to manual review. The coverage percentage is `100 × confirmed exact or alias matches / distinct JD keywords`, rounded to one decimal place. Partial matches contribute zero to the numerator, and an empty keyword set yields `null`. Coverage measures the rules used by this project.

### Configure Gemini and review suggestions

To enable suggestions, set the key in the backend CMD window before starting or restarting Uvicorn. The model setting is optional and defaults to `gemini-3.5-flash-lite`. The backend reads these settings from its environment. [Gemini API keys](https://aistudio.google.com/docs/api-key)

```cmd
cd backend
set "GEMINI_API_KEY=your-key-here"
set "GEMINI_MODEL=gemini-3.5-flash-lite"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Click **Generate AI Improvements** to send eligible resume lines, allowed replacements, and relevant skill terms to Gemini. The backend builds candidates for the summary, skills, projects, and experience using approved alias expansions and concise wording substitutions. Matching recognizes broader aliases such as HTML5 for HTML, while wording edits preserve the version or plural form in the original. Gemini selects and ranks candidates using structured JSON. The backend keeps exact candidate ID and replacement pairs that pass its wording rules and adds the original excerpt, reason, and verification status. [Gemini structured output](https://aistudio.google.com/docs/structured-output)

Use **Accept** or **Reject** on each suggestion. **Edit** allows source-equivalent wording. **Undo Draft** discards unsaved changes. Saving or restoring wording requires a new approval. Open **View approved resume content** to check the draft before exporting. The **Proposed · source-verified** column reruns the matcher on the accepted source-backed edits. Self-reported additions are labeled separately in the preview and can be included in ATS-friendly PDFs after approval. Original-layout export supports compatible replacements of existing lines.

The request excludes the contact section, full documents, and lines with detectable contact details. The API key stays on the backend. Calls happen only when requested and when eligible edits exist. Results are reused for unchanged inputs, including an empty valid selection. Provider failures leave the comparison available for retry. Rule-based matching and review work locally. Exact section text, a finite skill dictionary, and conservative wording substitutions limit the range of matches and suggestions.

### Matching API and private files

| Endpoint | Behavior |
| --- | --- |
| `GET /api/matches/sources` | Lists saved resume and job IDs and labels for the selectors |
| `POST /api/matches` | Takes `resume_id` and `job_id` and returns a saved comparison |
| `GET /api/matches/{match_id}` | Returns the comparison and any saved suggestion decisions |
| `GET /api/matches/{match_id}/editor` | Returns saved resume sections, job details, approved preview, and current and proposed coverage |
| `PUT /api/matches/{match_id}/skills` | Saves a review of a missing or partial keyword, with a source and sentence for self-reported evidence |
| `POST /api/matches/{match_id}/optimize` | Returns the comparison with `optimization` populated |
| `PATCH /api/matches/{match_id}/suggestions/{suggestion_id}` | Takes `{"approval_status":"accepted"}` or `{"approval_status":"rejected"}` and returns the updated comparison |
| `PUT /api/matches/{match_id}/suggestions/{suggestion_id}/edit` | Saves equivalent wording using `replacement`, or restores the AI wording with `null`. A change resets approval to pending. |

Use [Swagger UI](http://127.0.0.1:8000/docs) or send a comparison from CMD with existing IDs.

```cmd
set "RESUME_ID=replace-with-resume-uuid"
set "JOB_ID=replace-with-job-uuid"
curl.exe -i -H "Content-Type: application/json" --data "{\"resume_id\":\"%RESUME_ID%\",\"job_id\":\"%JOB_ID%\"}" http://127.0.0.1:8000/api/matches
```

A comparison includes the three keyword arrays, `relevant_projects`, `relevant_experience`, `required_qualifications_needing_review`, and keyword coverage. An abbreviated response for one matched keyword follows.

```json
{
  "match_id": "df5aa824-3728-534b-9980-39b583b21b15",
  "resume_id": "5d15d6f4-604f-4f29-80ac-e950cf7c63f5",
  "job_id": "64bbfed6-9900-49da-9ec6-34a124666249",
  "job_title": "Backend Developer",
  "matched_skills": [{"keyword": "JavaScript", "priority": "required", "match_type": "alias", "resume_evidence": [{"section": "technical_skills", "line_number": 1, "text": "JS", "matched_text": "JS"}], "jd_evidence": [{"field": "skill", "value": "JavaScript", "snippet": "JavaScript is required", "line_number": 2, "classification": "required", "matched_text": "JavaScript"}]}],
  "partial_matches": [],
  "missing_keywords": [],
  "relevant_projects": [],
  "relevant_experience": [],
  "required_qualifications_needing_review": [],
  "keyword_coverage": {"matched": 1, "total": 1, "percentage": 100.0},
  "optimization": null
}
```

An optimization adds the following object to the same response.

```json
{
  "status": "completed",
  "suggestions": [{
    "suggestion_id": "57688ad5-3422-424a-8a65-613cbbe1311e",
    "section": "technical_skills",
    "original_text": "JS",
    "suggested_replacement": "JavaScript",
    "relevant_jd_keywords": ["JavaScript"],
    "supporting_resume_evidence": [{"section": "technical_skills", "line_number": 1, "text": "JS", "matched_text": "JS"}],
    "reason": "Use clearer wording or standard skill names for terms supported by this resume line.",
    "verification_status": "verified_against_source",
    "approval_status": "pending"
  }],
  "discarded_suggestions": 0
}
```

The API returns `404` for missing records, `422` for invalid inputs, and `409` for invalid records, concurrent updates, or approvals that fail source verification. Gemini errors use `429` for rate limits, `503` for unavailable credentials, `504` for timeouts, and `502` for other provider or validation failures. Matches are private UTF-8 files under `backend/private_matches/<match_id>.json` with per-match lock files. They contain the comparison and exact edit candidates. Decisions update only the match, and writes publish complete files atomically. Comparison IDs incorporate the source snapshots and algorithm version. A deployment has one workspace whose token grants access to all its records.

## Generate an ATS-friendly resume

1. Upload and extract a resume, analyze a JD, and select both under **Resume vs JD matching**. Click **Compare Resume with JD**.
2. To apply suggested wording, generate suggestions and click **Accept** on the edits you want. The **ATS Resume Generator** shows the approved count. Zero approvals exports the original extracted wording, with the template's formatting.
3. Click **Generate ATS Resume**. Check the page count and applied edit count, then inspect the inline PDF. **Open preview in new tab** uses the same PDF and **Download PDF** saves a copy. Export works locally and requires a Gemini key only if you also request new wording suggestions.

The backend reads the stored comparison, extraction JSON, JD when rechecking approved edits, and original file. It revalidates accepted edits against the saved candidates and the current source at the exact section and line. Pending and rejected edits are excluded. Stale or unsupported approvals are counted and skipped. The service modifies a copy of the extracted sections and checks that the source file has the same bytes before and after rendering.

Templates use escaped HTML and CSS with PyMuPDF Story, which is already installed with the backend. The Letter-size layout flows in one column from the name and contact information through **Professional Summary**, **Technical Skills**, **Work Experience**, **Projects**, **Education**, and **Certifications**. Additional extracted text follows under **Additional Information**. Explicit bullets and recognized Word bullet styles are rendered as lists. Clear standalone dates use a consistent format such as `Jan 2022 – Sep 2024`. Dates within prose retain their wording. Valid email and web links, including LinkedIn and GitHub, are clickable. Supported link destinations recovered from the source may appear in a **Links** section.

The exporter opens the generated PDF again, checks every page for extractable text, compares the reading order and text to the prepared content, verifies link annotations, and reports the page count. It lets the content flow onto additional pages at the same font size and margins. The export is designed for ATS parsing, and employer systems may parse it differently.

### Generation API and storage

| Endpoint | Behavior |
| --- | --- |
| `POST /api/resumes/generate` | Accepts `resume_id` and `match_id` for that resume. Validates and saves the PDF before returning HTTP `201`. |
| `POST /api/resumes/generate/compatibility` | Reports whether approved edits can preserve the original PDF layout. |
| `GET /api/resumes/{resume_id}/original-pdf?match_id={match_id}` | Previews the validated original PDF for the supplied comparison. |
| `GET /api/resumes/generated/{generation_id}` | Returns the completed generation metadata. |
| `GET /api/resumes/generated/{generation_id}/download` | Returns the PDF as an attachment. Pass `?inline=true` to open it in a PDF viewer. |

Each export gets a fresh UUID. The API returns `generation_id`, `resume_id`, `match_id`, `mode`, `status`, `page_count`, and the approved, applied, and skipped suggestion counts. Files are saved as `backend/private_generated_resumes/<generation_id>/resume.pdf` and `<generation_id>.json` by default. The folder is Git-ignored. PDF and metadata responses use `Cache-Control: no-store`.

From CMD with the backend running and an existing comparison, substitute the UUIDs from the responses:

~~~
set "RESUME_ID=replace-with-resume-uuid"
set "MATCH_ID=replace-with-match-uuid"
curl.exe -i -H "Content-Type: application/json" --data "{\"resume_id\":\"%RESUME_ID%\",\"match_id\":\"%MATCH_ID%\"}" http://127.0.0.1:8000/api/resumes/generate
set "GENERATION_ID=replace-with-generation-uuid"
curl.exe -i "http://127.0.0.1:8000/api/resumes/generated/%GENERATION_ID%"
curl.exe -f -o ats-resume.pdf "http://127.0.0.1:8000/api/resumes/generated/%GENERATION_ID%/download"
~~~

The API returns `404` for missing records, `409` for invalid stored records or a source file that changed during generation, `422` for invalid IDs, mismatched pairs, or an unrenderable PDF, and `500` for storage or file I/O errors.

### Export limits

- The quality of the output depends on the existing extraction. Source layout and content outside extraction coverage may require review before using the PDF.
- Font fallback covers many scripts. When characters, reading order, or links cannot be reproduced and verified, generation returns an error so you can review the source. An export can contain up to 100 pages.
- The inline preview uses the browser's PDF viewer. You can download the PDF and open it in a local viewer if PDF embedding is disabled.
- Each export remains available under its generation ID on disk. A browser tab restores its most recent comparison and compatible saved export for each mode after refresh. Operators manage retention.

### Export an original-layout PDF

After reviewing edits, click **Generate Original-Layout PDF**. For a compatible source PDF, the backend checks geometry, text, annotations, and rendering before saving the edited file. With zero approvals it can copy a compatible original. Each successful export has its own preview and download. When the layout check fails, the UI shows the reason and leaves ATS export available. DOCX resumes can use ATS export.

## Active master resume, history, and reuse

1. Upload a resume once and select **Set as active master**. Resume selectors show the filename without its extension. Earlier uploads without a recorded filename show a short resume ID. Open **Rename or review selected resume** to inspect the content and save a display name. Setting the master extracts it if needed. The selection persists in the workspace and can be replaced without changing earlier uploads or exports.
2. Use **Master Resume** to manage the active master and **Resume Library** to browse saved PDFs. Search by name, job title, skill, or keyword. Filter by target role, export mode, or favorites, and sort by date, name, or favorites. Open a version to preview, download, name, favorite, or delete it. **Resume History** displays 20 versions per page and compares saved section text from the same source resume.
3. Analyze a new JD. **Recommended Saved Resumes** compares all available versions using their saved content and shows up to three ranked results. **Preview Resume** opens the saved version, and **Use This Resume** downloads it for manual reuse. If coverage is too low, **Create New Optimized Resume** selects the active master and JD for your next comparison. You control comparison, suggestions, and generation.

Set `RESUME_REUSE_THRESHOLD` in the backend environment to an integer from 1 to 100. The default is 80%. Reuse requires overall confirmed keyword coverage and, when present, confirmed required-skill coverage to meet that threshold. Preferred skill coverage, project and experience evidence, and qualification excerpts help explain ranking. The reported percentages measure internal keyword coverage. Review missing or partial evidence and qualifications against the job description.

New exports save immutable structured content and applied suggestion evidence alongside the PDF. Older exports enter history under their original generation ID and are indexed from PDF text on demand. Historical dates and approval provenance remain blank when unrecorded. Confirming delete removes an export from history, recommendations, and authenticated downloads by saving a deletion marker. The private bytes remain on disk for operator-managed retention.

### Remove uploaded resumes or saved JD analyses

Open **Master Resume > Manage uploaded resumes** or **JD Analyzer > Manage saved JD analyses**. Select the trash button beside the original filename or job title and confirm. Saved JDs also show a short ID and the company when the description includes a labeled company, employer, or organization line.

Deleting a source removes it from active selectors. Deleting the active master clears the active selection, and deleting the selected JD clears its selector and current analysis. You can still reopen saved comparisons through Resume History and use their generated PDFs in Smart Resume Reuse.

| Endpoint | Behavior |
| --- | --- |
| `DELETE /api/resumes/{resume_id}` | Archives a validated original PDF or DOCX upload UUID. |
| `DELETE /api/jobs/{job_id}` | Archives a saved job analysis UUID. |

Both endpoints use the workspace authentication policy. Success and repeated deletion of an archived source return `204`. Unknown IDs return `404` and malformed UUIDs return `422`. The upload endpoint only accepts IDs present in original upload storage.

This is soft deletion. The app atomically writes a deletion marker next to the selected source. It preserves the original file or JD, upload metadata, extraction snapshots, saved matches, and every saved PDF and history record. It never cascades into generated files. There is no permanent purge or restore UI. Keep the archived records and markers in backups and storage maintenance. Removing source bytes manually can break older comparisons and original-layout editing.

## Run automated tests

Run from the repository root in CMD. Backend tests generate synthetic documents in temporary storage.

```cmd
cd backend
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

With Microsoft Edge installed, run the browser integration test from a separate CMD window at the project root. It starts its own servers on ports `5194` and `8054` and uses a synthetic PDF and an isolated data directory. It exercises the tailoring workflow, active master persistence, both PDF modes, history controls, reuse recommendations, refresh, and a backend restart. Backend and browser tests mock Gemini.

```cmd
cd frontend
npm run build
node tests\jd-analyzer.browser.mjs
```

The [project documentation](docs/project.md) describes the APIs, storage, and access model. The [Step 9 test report](docs/step9-test-report.md) contains verification results, API examples, and manual testing commands.
