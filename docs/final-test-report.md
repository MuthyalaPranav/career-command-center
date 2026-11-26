# Step 8 final test report

**Status: NOT PRODUCTION READY.** Local application verification passed. Release verification still requires the actual HTTPS ingress, a mounted private production volume with a tested backup and restore, a live Gemini credential, and a Git checkout for the tracked-file audit. This workspace supplied a source snapshot without Git metadata. No production deployment was performed.

## Results on November 19, 2026

Windows, Python 3.13.5, Node.js 26.13.1, Vite 8.3.8, and Microsoft Edge headless. Application counts below use the final successful runs. The browser script counts as one test with several assertions. Build and audit commands are listed separately.

| Application tests | Executed | Passed | Failed | Blocked in suite | Skipped in suite |
| --- | ---: | ---: | ---: | ---: | ---: |
| Backend `unittest discover -s tests` | 65 | 65 | 0 | 0 | 0 |
| Frontend Edge integration `node tests/jd-analyzer.browser.mjs` | 1 | 1 | 0 | 0 | 0 |
| **Total** | **66** | **66** | **0** | **0** | **0** |

The backend suite completed in 47.9 seconds. Backend and browser automation used synthetic documents, synthetic job descriptions, and mocked Gemini responses. The browser test verified token rejection and unlock, upload and extraction, JD loading and error states, keyword coverage, persistent Accept and Reject actions, both PDFs in preview and download, and restoration after a browser refresh and a full Uvicorn restart. Viewport checks covered desktop, tablet, and mobile in Edge. It observed zero unexpected browser runtime exceptions.

| Additional check | Result |
| --- | --- |
| Frontend `npm run build` | Passed. TypeScript and Vite completed. JS output was 248.91 kB, 76.03 kB gzip. |
| `npm audit --json` | Passed. Zero known frontend advisories returned. |
| `pip check` | Passed. Installed backend requirements were consistent. |
| `pip-audit --path backend/.venv/Lib/site-packages` | Initial scan flagged pip 25.1.1 with 12 entries covering six advisory IDs. The follow-up scan after upgrading pip to 26.3 found zero known advisories. |
| `git status --short` and repository discovery | Blocked. Neither the workspace nor its parent exposed a Git repository to these commands. The ignore rules were inspected. |

## Verified behavior

- **Upload and extraction.** PDF and DOCX validation, missing and invalid IDs, malformed input, the 5 MiB boundary including streaming uploads, structured extraction JSON, and unchanged source files. Repeat extraction reused the valid saved snapshot. Invalid snapshots were rebuilt atomically.
- **JD, matching, and Gemini.** Canonical keywords and evidence, required and preferred classifications, exact and alias matches, partial evidence, missing terms, invalid JSON, concurrent comparison and optimization, provider refusal, rate limits, timeouts, invalid model output, and retry behavior. The provider receives constrained candidates. Tests verify the backend rejects unsupported or modified candidate output and refuses an unsupported stored suggestion at approval.
- **PDF quality and integrity.** ATS output contained searchable, ordered text and supported links. Original-layout tests checked geometry, glyphs, visual changes, preserved links and annotations, and compatibility failures. Tests verified that generation applies source-verified approvals only, excludes pending and rejected edits, preserves original bytes and extraction JSON, and validates the selected resume against the match. Missing and mismatched IDs, simulated storage failures, concurrent generation, and failed exports were covered.
- **Private access and errors.** Tests exercised missing and invalid bearer credentials, a successful authenticated download, explicit CORS allowlists and preflights, token-free loopback checks, production configuration guards, oversized JSON, and sanitized validation and server errors.

## Findings and fixes

| Finding | Final behavior |
| --- | --- |
| Private records and PDF URLs were reachable with a UUID and no credentials. | Production requires a workspace bearer token before reading or parsing private requests. Token-free development requires a loopback client and host. Health exposes liveness only. |
| The browser had ATS export only, and refresh discarded the active comparison and previews. | Both export modes are wired to the UI. It uses one authenticated fetch per generated PDF for preview and download and restores the most recent comparison, decisions and available exports within the tab. |
| PDF upload accepted active document objects, and DOCX checks covered only selected XML parts. | PDF checks cover active actions and attachments for upload and layout serving. DOCX checks bound archive and XML expansion and reject DTDs, macros and embedded active objects. UUIDs remain the only storage path input for records. |
| Approval trusted the stored verification marker. | Accept revalidates the exact candidate against the saved resume and JD while holding the match lock. Export independently revalidates approvals. |
| Validation responses could echo request content. Exception logging could disclose private paths. | Validation errors omit input values. API responses use `no-store`, unexpected failures return a generic message, and app-level storage errors log only the failing operation. Production instructions disable access logs that would expose record URLs. |
| Repeat extraction reread and reprocessed original documents. | Valid snapshots are reused. A regression test verifies PDF and DOCX reuse. The original-layout UI uses the result of generation directly and avoids a duplicate compatibility render. |
| File paths defaulted exclusively to app-local storage. | All five stores accept a common configured persistent root. A real backend process restart against the same test storage preserved access to saved records and both PDFs. |
| The installed pip version had known advisories. | Upgraded the project virtual environment to pip 26.3 and documented an installer minimum of 26.2 for setup. |

## Blocked and unexecuted checks

| Check | Status and reason |
| --- | --- |
| Live Gemini end-to-end smoke test | Blocked by the absence of a deployment credential. Provider contract tests used mocks. |
| HTTPS ingress, production browser origin, runtime limits, persistent mount and backup restoration | Blocked by the absence of provisioned release infrastructure. Local process restart and configuration validation ran. |
| Tracked or historically committed environment files and private artifacts | Blocked by missing Git metadata. `.gitignore` already excludes `.env` variants, local private artifact directories, logs, and build outputs while retaining `.env.example`. |
| Full cross-browser and physical-device matrix | Unexecuted. Edge emulated the tested viewports. |
| Sustained-load benchmark, document-parser fuzzing, and employer ATS compatibility | Unexecuted. Automated coverage used representative synthetic inputs, concurrent request tests, and PDF content and layout validation. |

## Remaining limits

The release supports one authenticated workspace per backend instance. Each token grants access to all records in that workspace. Public multi-user hosting requires per-user identity and record isolation. Keep document processing under host resource limits and apply dependency updates. OCR is unavailable, complex layouts can require ATS fallback, and the English rules and skill dictionary limit extraction and matching. Operators manage backup, retention, and deletion. The deployment procedure and remaining release checks are in the [deployment guide](deployment-guide.md).

## File inventory

**Created**

- `backend/app/api_protection.py`, `backend/app/settings.py`, `backend/tests/test_release_security.py`
- `docs/final-test-report.md`, `docs/deployment-guide.md`

**Modified**

- Backend app. `generation_routes.py`, `generation_storage.py`, `job_routes.py`, `job_storage.py`, `main.py`, `match_routes.py`, `match_storage.py`, `pdf_layout_editing.py`, `resume_extraction_models.py`, `resume_generation.py`, `resume_optimization.py`, `resume_routes.py`, `resume_storage.py`, `resume_validation.py`
- Backend tests. `test_job_analysis.py`, `test_resume_extraction.py`, `test_resume_generation.py`, `test_resume_matching.py`, `test_resume_upload.py`
- Frontend source. `App.tsx`, `ATSResumeGenerator.tsx`, `JDAnalyzer.tsx`, `MatchDashboard.tsx`, `ResumeExtraction.tsx`, `ResumeUploader.tsx`, `resumeApi.ts`
- Browser integration. `frontend/tests/jd-analyzer.browser.mjs`
- Configuration and documentation. `.env.example`, `README.md`, `docs/project.md`
