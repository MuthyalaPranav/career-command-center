# Deployment guide

Deploy one private workspace per backend instance. The React frontend is a static build. FastAPI runs as a long-lived process on a host with a private persistent volume. Keep the volume outside the frontend publish directory and the source checkout. A static hosting service can serve `frontend/dist`. Its ephemeral serverless filesystem cannot hold the backend's records.

## Prerequisites

- Python 3.13 with current patch updates, Node.js 22.12 or newer, and npm. The local commands use the Windows Python launcher and Command Prompt.
- For production, a dedicated non-admin service account, private persistent storage and backups, HTTPS endpoints, and a reverse proxy or managed ingress with request and resource limits.
- A Gemini API key and a model available to that key. The API host needs outbound HTTPS to Google's Gemini API. Use synthetic content for the first live provider check.
- Microsoft Edge for the Windows browser integration test.

## Environment variables

| Variable | Process | Local default | Production |
| --- | --- | --- | --- |
| `RESUME_ENV` | Backend | `development` | Set to `production`. This requires access control, persistent storage configuration, a provider key, and HTTPS origins. It disables API documentation routes. |
| `RESUME_WORKSPACE_TOKEN` | Backend | Optional on loopback | Required. Generate with Python's `secrets.token_urlsafe(32)`. Supply it to the backend secret store and authorized workspace operators. |
| `RESUME_DATA_DIR` | Backend | `backend/` | Required. Set an existing absolute directory on a persistent private volume. |
| `RESUME_REUSE_THRESHOLD` | Backend | `80` | Optional integer from 1 to 100. Sets the minimum confirmed overall and required-skill coverage for a reuse recommendation. |
| `FRONTEND_ORIGINS` | Backend | `http://localhost:5173` | Required in practice. Use a comma-separated list of exact HTTPS origins without paths or trailing slashes. `FRONTEND_ORIGIN` remains a fallback for older settings. |
| `GEMINI_API_KEY` | Backend | Optional for the local workflows other than suggestions | Required. Use a restricted provider key in the backend secret store. |
| `GEMINI_MODEL` | Backend | `gemini-3.5-flash-lite` | Optional override. Verify access with the deployment key. |
| `VITE_API_BASE_URL` | Frontend build | `http://127.0.0.1:8000` | Set to the HTTPS API base URL before building. Static assets contain this public URL. |

`.env.example` is a reference. The backend reads process environment variables. Keep the workspace token and Gemini key out of all Vite variables, build arguments exposed to the frontend, and URL query strings. The browser prompts for the workspace token and keeps it in tab-scoped storage. API clients send it in `Authorization: Bearer <token>` and should use a secret-injection mechanism appropriate for their environment.

## Local setup using Windows CMD

Open two CMD windows at the project root.

Backend window

```cmd
cd backend
if not exist .venv\Scripts\python.exe py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2"
.venv\Scripts\python.exe -m pip install -r requirements.txt
set "RESUME_ENV=development"
set "FRONTEND_ORIGINS=http://localhost:5173"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Set `GEMINI_API_KEY` in the backend environment before starting Uvicorn to enable live suggestions. For a local authentication check, generate a development token with `.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"` and set `RESUME_WORKSPACE_TOKEN` before startup.

Frontend window

```cmd
cd frontend
npm ci
set "VITE_API_BASE_URL=http://127.0.0.1:8000"
npm run dev
```

Open the UI at `http://localhost:5173`. From a third CMD window, `curl.exe -i http://127.0.0.1:8000/api/health` should return HTTP 200 with `{"status":"ok"}`.

## Persistent storage

The backend creates these subdirectories under `RESUME_DATA_DIR`.

| Directory | Contents |
| --- | --- |
| `private_uploads` | Original PDFs and DOCX files plus upload display-name sidecars |
| `private_extractions` | Structured extraction snapshots |
| `private_job_analyses` | Original job descriptions and parsed analysis |
| `private_matches` | Comparisons, suggestions, saved decisions, and coordination files |
| `private_generated_resumes` | Final PDFs, immutable snapshots and provenance, preferences, and deletion markers |
| `private_workspace` | Active master resume selection |

Grant read/write access to the backend service account and authorized backup operators. On Windows, configure NTFS ACLs on the root. On Linux, use restrictive ownership, permissions and umask. Confirm the volume supports same-directory atomic replacement and file locks. Use disk quotas and monitoring, enable encryption at rest, and back up the six directories together. Restore a backup into an isolated environment and verify representative records and PDFs. Check the mount and its contents separately from API liveness.

To migrate existing records, stop writes and copy the existing private directories with their original names into the new root. Include deletion markers and preferences so deleted entries stay hidden and names carry over. Start the backend with the new setting and verify known resume, match, and generation IDs. Keep the previous copy until verification succeeds. The single-instance deployment uses the existing filesystem backend. A deployment that needs multiple independent workspaces can use separate backend instances, volumes, and tokens. Shared multi-user hosting would require per-user identities, record ownership and storage isolation.

Step 9 indexes readable legacy generated PDFs when history or recommendations first need their content. Keep each generation directory intact, including its manifest, PDF, cache, preferences, and any deletion marker. Legacy entries keep their existing generation IDs. Include `X-Total-Count` along with the API's CORS headers through the proxy so the browser can paginate history. Verify that the history, download, recommendation, and master endpoints require the workspace token in production. See the [Step 9 test report](step9-test-report.md) for local smoke checks.

## Deploy

1. Provision the service account, storage volume, TLS ingress, and host secrets. Configure the above backend variables in the service manager. Use a deployment identity with access only to this workspace.
2. Install backend dependencies under the service account from a vetted package registry. Record the resolved versions and run `pip check` and `pip-audit` on the release environment. On Windows, run the following from `backend` after environment configuration.

   ```cmd
   if not exist .venv\Scripts\python.exe py -3.13 -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2"
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   .venv\Scripts\python.exe -m pip check
   .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
   ```

   On a Linux service host use the environment's `.venv/bin/python` with the same Uvicorn arguments. Run under a supervisor with restarts and memory, CPU and temporary storage limits. If the proxy is on a separate host, use a private listener restricted to the proxy. Configure accepted hostnames, HTTPS, a request limit of about 6 MiB, connection deadlines and rate limits at the proxy. The provider timeout is 25 seconds per blocking network operation, so allow time for the request and application processing. Forward the authorization header and preserve CORS response headers. Leave request bodies, authorization headers, UUID-bearing URLs and query strings out of access logs. [FastAPI HTTPS deployment](https://fastapi.tiangolo.com/deployment/https/)
3. Build the frontend from `frontend` with the actual backend URL. Publish only `dist` to the static host over HTTPS.

   ```cmd
   npm ci
   npm audit
   set "VITE_API_BASE_URL=https://api.example.com"
   npm run build
   ```

4. Smoke-test against the release infrastructure. Check public liveness, verify unauthenticated requests to known private records return `401`, and confirm an explicit allowed origin passes CORS. Complete the synthetic workflow with a live Gemini key, download both PDFs, restart the backend, and verify the same records and bytes are available on the persistent volume. Verify the backup/restore procedure and audit the checked-out Git tree before publishing.
5. Monitor availability, disk use, error counts, and Gemini rate limits. Rotate credentials through the service manager, restart the backend, and re-unlock the workspace with the new token. Establish an operator-managed retention policy for artifacts and backups.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Backend will not start in production | Check the absolute existing data path, service-account permissions, token format, Gemini key, and explicit HTTPS origins. |
| Workspace returns `401` | Enter the workspace token issued for this backend. After rotation, reload the tab and use the new token. |
| Browser cannot reach the API or reports CORS | Confirm the build used the correct HTTPS API URL and the browser's exact origin appears in `FRONTEND_ORIGINS`. Rebuild after changing the Vite URL. |
| Suggestion request fails | `503` indicates missing or rejected credentials, `429` indicates rate limiting, `504` indicates a timeout, and `502` can indicate provider, model or response validation failure. Check access to the configured model with the deployment key. |
| No records after restart | Check the service's resolved `RESUME_DATA_DIR`, volume attachment, and permissions under the same service identity. Verify a known match ID and restore from backups if needed. |
| Original-layout export fails | Check the displayed compatibility reason and use ATS export when the original PDF cannot be edited safely. For an unavailable embedded viewer, use the named download link. |
