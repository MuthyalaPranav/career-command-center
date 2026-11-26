from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api_protection import APIProtection
from .generation_routes import router as generation_router
from .history_routes import router as history_router
from .job_routes import router as job_router
from .match_routes import router as match_router
from .resume_routes import router as resume_router
from .settings import FRONTEND_ORIGINS, PRODUCTION, WORKSPACE_TOKEN, validate_settings

validate_settings()
app = FastAPI(
    title="AI Resume Tailor API", docs_url=None if PRODUCTION else "/docs",
    redoc_url=None if PRODUCTION else "/redoc", openapi_url=None if PRODUCTION else "/openapi.json",
)

app.add_middleware(APIProtection, token=WORKSPACE_TOKEN, origins=FRONTEND_ORIGINS)
if not PRODUCTION and not WORKSPACE_TOKEN:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Total-Count"],
)

app.include_router(resume_router)
app.include_router(generation_router)
app.include_router(history_router)
app.include_router(job_router)
app.include_router(match_router)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    details = [{key: item[key] for key in ("type", "loc", "msg")} for item in error.errors()]
    # Extra field names can also be supplied by a caller.
    for item in details:
        if item["type"] == "extra_forbidden":
            item["loc"] = ("body",)
    return JSONResponse({"detail": details}, status_code=422)


@app.get("/api/access", include_in_schema=False)
@app.get("/api/health")
async def health() -> dict[str, Literal["ok"]]:
    return {"status": "ok"}
