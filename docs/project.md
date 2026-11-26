# AI Resume Tailor

## Project goal

Help job seekers tailor a resume to a specific job description while preserving the facts of their experience. Users can review suggested edits before exporting a PDF.

## Implemented workflows

- Accept a resume in PDF or DOCX format and a job description.
- Identify relevant experience and suggest edits grounded in the resume.
- Let the user review, accept, or reject suggested changes.
- Support original-layout PDF editing that retains the source document's design where feasible.
- Support ATS-friendly PDF regeneration using a structured template with selectable text and a predictable reading order.

## Architecture and technologies

The monorepo has two independently runnable applications. `frontend/` uses React and TypeScript for the interface, Vite for development and builds, and Tailwind CSS for styling. `backend/` uses Python and FastAPI for HTTP endpoints and Uvicorn to serve the API. `docs/` holds the project documentation.

The homepage includes a reusable uploader for a single PDF or DOCX file up to 5 MB. The frontend sends a multipart request to `POST /api/resumes/upload`. The backend reads the file in bounded chunks, checks the signature and basic document structure with pypdf or Python's ZIP support and defusedxml, and stores the validated file under a UUID in `backend/private_uploads/`. The upload response provides file metadata and the resume ID. The API also exposes `GET /api/health` and allows the local frontend origin through CORS.

After upload, the user can request extraction through `GET /api/resumes/{resume_id}/extract`. The backend resolves the UUID against the private upload directory and reads the original file without saving changes. PyMuPDF reads PDF text blocks in approximate visual order, with 1-based pages and source geometry at block, line, and span level. `python-docx` walks body paragraphs and tables in document order and records paragraph styles, heading levels, and cell locations. The rule-based section parser groups extracted text under recognized resume headings and preserves unclassified text. The frontend displays the populated sections and offers a raw text view.

The backend saves its extraction response as indented UTF-8 JSON in `backend/private_extractions/<resume_id>.json` before returning success. Repeat requests reuse valid snapshots. A stale or invalid snapshot is rebuilt from the original and replaced atomically. Save failures return HTTP `500` with a user-facing message.

PDF scans require OCR. Mixed PDFs report detectable image-only pages, and visually complex PDFs can produce imperfect reading order. DOCX extraction covers main-body paragraphs and tables. Section detection works best with standard English headings. See the [README](../README.md) for Windows CMD setup, the API response and error codes, dependencies, and test commands.

The JD analyzer is an independent workflow on the homepage. `POST /api/jobs/analyze` validates pasted text and applies deterministic headings, phrase patterns, and a separately maintained technical skill dictionary. It returns a job title, requirements and responsibilities, canonical ATS keywords, and evidence with the original terminology and requirement classification. Direct English wording and terms in the dictionary provide the best coverage.

Each successful analysis returns `job_id` and saves a UTF-8 JSON record at `backend/private_job_analyses/<job_id>.json`. The record contains the complete API response and the original job description. The shared private JSON writer publishes complete files atomically. The [README](../README.md) includes the JSON fields, a CMD request example, and automated test commands.

## Resume and JD matching

The dashboard loads extracted resumes and analyzed JDs from `GET /api/matches/sources`. Selecting a pair submits `POST /api/matches` with `{"resume_id":"<UUID>","job_id":"<UUID>"}`. The service compares the saved records using the shared skill dictionary. It checks confirmed occurrences in skills, projects, and experience, distinguishes exact JD wording from aliases, and uses summary-only, tentative, and related terms as partial evidence. Empty evidence means no supporting text was found. Exact results retain the matching source wording even when other excerpts hit the display limit. Qualifications flagged for manual review carry their JD evidence. Keyword coverage divides confirmed exact and alias matches by the number of distinct JD keywords.

`GET /api/matches/{match_id}` reads a saved comparison. The response includes `matched_skills`, `partial_matches`, `missing_keywords`, `relevant_projects`, `relevant_experience`, `required_qualifications_needing_review`, `keyword_coverage` (for example, `{"matched":2,"total":3,"percentage":66.7}`), and `optimization`, which starts at `null`.

The comparison dashboard separates the resume inventory and evidence from required and preferred JD skills, requirements, and responsibilities. Its editor endpoint derives current coverage from the saved extraction and recalculates proposed coverage after verified, accepted wording edits. Confirmed exact and alias matches contribute to the required, preferred, and overall internal metrics.

## Gemini optimization and review

Set `GEMINI_API_KEY` in the backend environment before launching Uvicorn. Optionally set `GEMINI_MODEL` to override `gemini-3.5-flash-lite`. `POST /api/matches/{match_id}/optimize` asks Gemini to rank source-preserving wording edits. The backend builds eligible edits from the resume and JD, preserves meaningful versions and qualifiers, and sends candidate lines as data. Pydantic validates Gemini's structured response and the backend checks each proposed replacement against the exact source-derived candidate. The resulting `optimization` contains `status`, `suggestions`, and `discarded_suggestions`. Each suggestion includes the original and replacement, relevant JD terms, source evidence, a reason, and a source verification status. `PATCH /api/matches/{match_id}/suggestions/{suggestion_id}` records `{"approval_status":"accepted"}` or `{"approval_status":"rejected"}` in the match record.

Match JSON, including the decisions, is saved atomically in `backend/private_matches/<match_id>.json`. A per-match file lock prevents concurrent optimization and approval writes. Repeated requests for the same source snapshots and matching rule version reuse the comparison and cached suggestions. Approval rechecks the exact replacement and source evidence against the saved resume and job analysis. Backend and browser automation use synthetic records and mock Gemini.

The editor stores manual wording separately from the original candidate. A changed draft must remain equivalent under the existing source-grounded substitutions, whitespace normalization, and an optional final period. Saving or restoring the AI wording resets approval to pending. Export also reruns validation.

Skill reviews use the same match lock and workspace authorization. For a missing or partially matched keyword, a user can leave it pending, mark it out of scope, or submit a source and an exact resume-ready sentence as self-reported evidence. Gemini works from the original source-backed candidates. Accepting a self-reported proposal adds that sentence to the approved preview and ATS export, with provenance saved in Resume History. The source-verified coverage figures exclude these sentences. Original-layout export requires an existing line position for each approved edit.

## ATS-friendly PDF generation

Step 6 adds export from the selected resume and JD comparison. The generator reads the latest saved comparison, checks that it belongs to the requested resume, and rebuilds eligible edits from the structured resume and JD when accepted edits exist. It applies a stored, source-verified acceptance only at its original section and line when the same candidate remains eligible. Pending, rejected, and unsupported edits stay out of the PDF. Source files and extraction snapshots are read-only during export.

The generation service hands prepared content to a separate HTML/CSS renderer using PyMuPDF Story. Letter-size pages use a single column with the name and contact information at the top, followed by Professional Summary, Technical Skills, Work Experience, Projects, Education, and Certifications. Additional extracted information and recovered link destinations follow. The renderer normalizes unambiguous standalone dates and uses additional pages when needed. Validation reopens the PDF, verifies each page has extractable text, compares that text in order to the prepared sections, and checks that the links survived rendering. Source bytes are also checked after rendering.

`POST /api/resumes/generate` takes `resume_id` and `match_id` and returns a completed generation UUID, page count, and approval counts. `GET /api/resumes/generated/{generation_id}` retrieves metadata. `GET /api/resumes/generated/{generation_id}/download` serves the generated PDF as an attachment, with `?inline=true` for the browser preview. Each export has a folder under `backend/private_generated_resumes/<generation_id>/` with `resume.pdf` and atomically published metadata. The dashboard lets users generate with zero approvals and shows when accepted edits were skipped.

The local exporter can use up to 100 pages. It relies on the saved extraction and the browser's PDF viewer, and reports unsupported text instead of publishing a lossy file. Employers use different parsing rules. See the [README](../README.md) for Windows CMD commands, API error behavior, and the backend and browser tests.

## Original-layout PDF generation

Set `mode` to `original_layout` when calling `POST /api/resumes/generate`. The layout service checks the source geometry against its extraction snapshot, plans verified edits, and validates text, visual differences, links, and annotations before publishing. `POST /api/resumes/generate/compatibility` runs the same checks and returns an explanation without publishing a PDF. `GET /api/resumes/{resume_id}/original-pdf?match_id={match_id}` serves a validated source PDF associated with the comparison. The browser uses the generation response to show compatibility issues and downloads each generated PDF once for both preview and download.

## Workspace access and deployment

The release supports one private workspace per backend. A bearer token authorizes all private API data and operations in that workspace. The frontend accepts the token at runtime and keeps it for the browser tab. Production requires a token, explicit HTTPS frontend origins, a backend Gemini key, and an existing absolute private storage root. Each record type retains its own subdirectory under that root. Public health returns liveness only. See the [deployment guide](deployment-guide.md) and [Step 8 test report](final-test-report.md).

## Resume history and smart reuse

The active master pointer lives in `private_workspace/master.json` under the data root. Select any saved upload whose content can be extracted. The backend publishes the pointer atomically after making sure the extraction is available. A resume's upload ID remains the provenance link for every export from that source, including exports created before the active master changes. Upload display names live in `private_uploads/<resume_id>.name.json`. New uploads start with their filename. Older uploads can be renamed in place.

Generation writes the PDF before atomically publishing its manifest. The manifest includes the rendered sections, original resume and comparison IDs, job reference, UTC creation time, export mode, and applied suggestion evidence. A separate preferences file holds the editable name and favorite flag. A per-version lock coordinates edits and deletion. The deletion marker hides the history entry and both download dispositions while keeping its private files available for operator retention. Legacy manifests keep their existing IDs. History indexes their actual PDF text once where readable, and uses the old comparison for recoverable job linkage. Unrecorded dates, keywords, and approval provenance stay unknown.

After JD analysis succeeds, the browser calls `POST /api/resumes/recommend` with the saved job ID. The backend compares the JD with all available structured export versions using deterministic skill matching. It checks required and preferred skills separately, gives exact and alias matches full credit, and shows contextual or partial matches separately. The ranking prioritizes versions meeting the configurable overall and required coverage threshold, then uses required, preferred, and overall coverage, qualification excerpts, and relevant experience and projects. Creation date and generation ID break ties. The API returns up to three results with evidence. Preview opens the existing export in My Resumes, and reuse downloads the saved PDF. The fallback preselects the active master and new JD for a user-initiated comparison.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/resumes/master` | Retrieve the active master pointer, or null. |
| `GET /api/resumes/master/options` | List uploaded PDF and DOCX resumes eligible for extraction. |
| `PATCH /api/resumes/master/options/{resume_id}` | Change an upload's display name. |
| `PUT /api/resumes/master` | Set the active master with `resume_id`. |
| `GET /api/resumes/history` | List saved summaries with `q`, `mode`, `favorite`, `sort`, `offset`, and `limit` query parameters. `X-Total-Count` gives the filtered total before pagination. |
| `GET /api/resumes/history/{generation_id}` | Retrieve a version and its immutable snapshot. |
| `PATCH /api/resumes/history/{generation_id}` | Update the name or favorite flag. |
| `DELETE /api/resumes/history/{generation_id}` | Remove a saved version from workspace access. |
| `GET /api/resumes/history/{generation_id}/download` | Download a PDF. Use `?inline=true` for preview. |
| `POST /api/resumes/recommend` | Rank saved versions against the analyzed `job_id`. |
| `GET /api/jobs/{job_id}/recommendations` | Existing read-only route for the same ranking. |

History defaults to newest first with a 20-item page and accepts a limit of 1–100. Sorting supports `newest`, `oldest`, `name`, and `favorites`. Unavailable entries can remain in history for management and are excluded from ranking. Invalid or missing IDs receive the corresponding validation or resource error. See the [Step 9 test report](step9-test-report.md) for examples and local verification.

## Uploaded resume and JD deletion

`DELETE /api/resumes/{resume_id}` checks the original upload directory for a regular PDF or DOCX file using the validated UUID. Generation manifests and PDF export directories are never used by this endpoint. Existing generated, ATS, original-layout, and manually edited variants continue to use the generation and history APIs with their existing provenance.

`DELETE /api/jobs/{job_id}` validates the stored JD record before archiving it. A `<uuid>.deleted.json` marker in the corresponding source directory is published atomically. Repeated or simultaneous deletion of the same source converges on the same marker. Active lists and new comparisons exclude archived sources. An active master pointer to a deleted upload resolves to null. Saved matches can still read the archived inputs, including the original PDF needed for original-layout editing, and generation already underway can finish.

Archiving preserves the original JD text, any company information it contains, title and keywords, upload filename metadata, and all associated match and generation snapshots. History can still display the recorded upload filename, and reuse continues to rank saved versions by their content. All existing history entries, downloads and legacy snapshots keep their existing storage and IDs. There is no automatic cleanup, permanent purge, or restore endpoint. Back up markers together with their source records, and retain those records during storage maintenance.

## Development phases

1. **Foundation.** Step 1 adds the repository structure, homepage, health endpoint, local CORS, and Windows setup instructions. Step 2 adds resume upload, document validation, local storage, and upload tests.
2. **Resume extraction and structured parsing.** Step 3 adds PDF and DOCX extraction, PDF layout metadata, rule-based sections, private JSON snapshots, and a review UI.
3. **Job description analysis.** Step 4 adds job description intake, rule-based requirements and ATS keyword extraction, supporting evidence, and private storage.
4. **AI-assisted tailoring and review.** Step 5 compares saved resumes and JDs and records decisions on Gemini-assisted wording suggestions.
5. **ATS-friendly PDF export.** Step 6 generates a searchable PDF with approved wording edits and adds preview and download.
6. **Original-layout editing.** Step 7 adds layout feasibility checks and source-preserving export.
7. **Integration and deployment readiness.** Step 8 connects original-layout export to the browser, adds private workspace access, hardens validation and approval, and verifies persistence with a backend restart.
8. **Resume history and reuse.** Step 9 adds a persistent master pointer, immutable export history, and deterministic recommendations for new job descriptions.
