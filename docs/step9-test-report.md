# Step 9 test report

## Persistence and history

The active master is a workspace-level pointer stored atomically under `RESUME_DATA_DIR/private_workspace/master.json`. Changing the pointer keeps previously uploaded files and prior generations intact. Upload names are stored separately alongside their source files.

Both export modes save a PDF and an immutable manifest under the generation ID. The manifest contains the source resume, comparison, and job references, creation time, structured content used for export, and applied suggestion evidence when available. Rename and favorite operations update a separate preferences file. Updates and confirmed deletion use the same per-generation lock. Deletion publishes a marker that hides the version from history, ranking, and downloads. Operators manage retention of the private files.

Readable old exports are indexed on demand from their own PDF text and keep their original generation IDs. Indexing is idempotent. Unknown historical dates, job metadata, and approvals remain unknown. Missing files and legacy PDFs without readable text are excluded from recommendations and shown as unavailable when their manifest remains readable.

## Recommendations

The backend compares every available version's saved content with the newly analyzed JD using the existing deterministic skill matcher. Exact and alias matches count toward internal keyword coverage. The response breaks out required and preferred skills and includes partial evidence, missing keywords, relevant projects and experience, and qualification excerpts for review.

`RESUME_REUSE_THRESHOLD` defaults to 80%. A reuse recommendation requires enough confirmed overall coverage and enough confirmed required-skill coverage when the JD has recognized required skills. Suitable versions rank first, followed by required, preferred, and overall coverage, qualification and project or experience evidence, creation time, and generation ID. The service returns up to three versions. With insufficient coverage, the frontend preselects the active master and the analyzed JD for a manual comparison. Ranking uses the saved data and makes no Gemini calls.

## API examples

`GET /api/resumes/master` returns the selected upload ID, or JSON `null` for a workspace with no active master.

```json
{
  "resume_id": "13c52d70-c388-4983-a19a-fcbc066b3bed"
}
```

`GET /api/resumes/history?mode=ats&sort=newest&offset=0&limit=20` returns an array. The response header `X-Total-Count: 1` indicates one matching version before pagination in this example.

```json
[
  {
    "generation_id": "86db479b-e075-4e83-b78e-ccf42abb1d80",
    "resume_id": "13c52d70-c388-4983-a19a-fcbc066b3bed",
    "match_id": "f2e25d03-ad08-56bc-9777-e156d152807d",
    "job_id": "c8fb99c4-466d-4cbd-a466-de1db54a8fb5",
    "mode": "ats",
    "job_title": "Python Developer",
    "created_at": "2026-11-19T12:00:00Z",
    "page_count": 1,
    "skills": ["JavaScript", "Python"],
    "keywords": ["Python", "JavaScript"],
    "status": "available",
    "source": "generated",
    "name": "Software Engineer Resume",
    "favorite": true
  }
]
```

An illustrative `POST /api/resumes/recommend` response is shown below. Candidate evidence arrays and other fields are omitted here for brevity. The request body is `{"job_id":"<analyzed-job-uuid>"}`.

```json
{
  "job_id": "99739122-2183-4210-aa64-750477022c3d",
  "active_master_id": "13c52d70-c388-4983-a19a-fcbc066b3bed",
  "recommended_generation_id": "86db479b-e075-4e83-b78e-ccf42abb1d80",
  "recommendation": "reuse",
  "threshold": 80,
  "candidates": [
    {
      "generation_id": "86db479b-e075-4e83-b78e-ccf42abb1d80",
      "keyword_coverage": { "matched": 2, "total": 2, "percentage": 100.0 },
      "required_coverage": { "matched": 2, "total": 2, "percentage": 100.0 },
      "preferred_coverage": { "matched": 0, "total": 0, "percentage": null },
      "suitable": true
    }
  ]
}
```

`GET /api/resumes/history/{generation_id}/download` returns `application/pdf` as an attachment. `?inline=true` returns the same bytes with an inline disposition. Deleted versions and files unavailable to history return `404`. The API rejects malformed UUIDs with `422` and unauthenticated production requests with `401`.

## Automated verification

Final runs on November 19, 2026 used Windows, synthetic resumes and job descriptions, and mocked Gemini.

| Check | Result |
| --- | --- |
| Backend `python -m unittest discover -s tests` | 76 passed, 0 failed, 0 skipped. Runtime 46.4 seconds. |
| Edge `node tests/jd-analyzer.browser.mjs` | 1 workflow passed, including a real Uvicorn restart and byte-for-byte equality of the recommendation and history downloads. |
| Frontend TypeScript `tsc --noEmit` | Passed. |
| Frontend `npm run build` | Passed. TypeScript and Vite completed. |

- Backend coverage included replacement of the active master, both export modes, exact PDF text for applied suggestions, preservation of source files and provenance, fixed-size history pages, filtering and sorting, preview and download headers, and safe legacy indexing without duplicate IDs.
- Ranking coverage included exact and alias matches, separate required and preferred counts, the top three, missing skills, an empty history, the threshold and fallback, and ranking against saved content after the original extraction changed.
- Access and concurrency tests exercised authentication on the new routes, malformed and missing IDs, conflict handling, and preservation of both preference edits after retry. The immutable manifest stayed unchanged.
- The Edge workflow exercised unlock, upload, master name and selection across restart, JD analysis, suggestion review, both PDF modes, the My Resumes master controls, recommendation highlight, preview, direct reuse, search, filter, sort, favorite, rename, and cancel and confirm deletion. Existing Step 1–8 backend and browser assertions ran as part of these suites.

## Manual testing in Windows CMD

From the project root, in one CMD window, start the backend on a test data root. Keep the same `RESUME_DATA_DIR` when restarting it.

```cmd
cd backend
if not exist .venv\Scripts\python.exe py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
set "RESUME_ENV=development"
set "FRONTEND_ORIGINS=http://localhost:5173"
set "RESUME_DATA_DIR=%TEMP%\resume-tailor-step9"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

From a second CMD window at the project root, start the frontend.

```cmd
cd frontend
npm ci
set "VITE_API_BASE_URL=http://127.0.0.1:8000"
npm run dev
```

1. Open `http://localhost:5173`. Upload a selectable-text synthetic PDF with a Skills section containing Python and JS. Name it, set it as the master, and run a comparison with a JD requiring Python and JavaScript.
2. Generate ATS and original-layout PDFs with zero approvals. Open **My Resumes**. Preview both versions, rename and favorite one, then search and filter for it.
3. Analyze a new JD requiring Python and JavaScript. Preview the recommended version, then click **Use This Resume** to download it. Analyze a JD requiring a missing skill such as Rust and verify **Create New Optimized Resume** selects the master and JD for comparison.
4. Refresh the page and restart Uvicorn. Verify the master, both exports, and saved preferences persist. Cancel one deletion and verify the version remains, then confirm it and verify the original master and other export remain.

An optional CLI smoke check from another CMD window can use IDs from the browser or API.

```cmd
set "API=http://127.0.0.1:8000"
set "JOB_ID=replace-with-analyzed-job-id"
set "VERSION_ID=replace-with-generation-id"
curl.exe -i "%API%/api/resumes/master"
curl.exe -i "%API%/api/resumes/history?mode=ats&limit=1&offset=0"
curl.exe -i -H "Content-Type: application/json" --data "{\"job_id\":\"%JOB_ID%\"}" "%API%/api/resumes/recommend"
curl.exe -f -o "%TEMP%\resume-tailor-step9-export.pdf" "%API%/api/resumes/history/%VERSION_ID%/download"
```

When testing with a workspace token, supply its bearer authorization header on each API request. Local manual ranking and zero-approval export run without a Gemini credential.

## Security and limits

All history, master, and recommendation endpoints use the existing workspace protection. The deployment model is one private data root and token per backend. Originals, extraction data, PDFs, and metadata stay in private storage. The browser fetches PDFs into temporary blob URLs for preview and download. Git tracking could not be inspected because this workspace has no Git metadata. The ignore rules cover the default storage directories. No deployment was performed.

Coverage is an internal keyword measure, and qualification evidence requires review. OCR and employer-specific ATS behavior remain outside this workflow. Legacy metadata gaps are retained and only readable versions contribute to ranking. History pagination limits each response, while the server still enumerates local manifests when filtering.

## File inventory for Step 9 completion

- Created `docs/step9-test-report.md`.
- Updated backend routes and history services in `backend/app/generation_routes.py`, `backend/app/history_routes.py`, `backend/app/main.py`, and `backend/app/resume_history.py`.
- Updated frontend pages and API helpers in `frontend/src/App.tsx`, `frontend/src/MyResumes.tsx`, `frontend/src/ReuseRecommendations.tsx`, `frontend/src/historyApi.ts`, and `frontend/src/useResumePDF.ts`.
- Expanded automated coverage in `backend/tests/test_resume_history.py` and `frontend/tests/jd-analyzer.browser.mjs`.
- Updated `README.md`, `docs/project.md`, and `docs/deployment-guide.md`.
