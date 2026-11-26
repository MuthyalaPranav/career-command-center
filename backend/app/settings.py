import os
import re
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
MODE = os.getenv("RESUME_ENV", "development")
PRODUCTION = MODE == "production"
WORKSPACE_TOKEN = os.getenv("RESUME_WORKSPACE_TOKEN", "")
STORAGE_SETTING = os.getenv("RESUME_DATA_DIR")
REUSE_THRESHOLD = os.getenv("RESUME_REUSE_THRESHOLD", "80")
DATA_DIRECTORY = Path(STORAGE_SETTING or Path(__file__).resolve().parents[1]).resolve()
FRONTEND_ORIGINS = [
    entry.strip() for entry in os.getenv(
        "FRONTEND_ORIGINS", os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"),
    ).split(",") if entry.strip()
]


def validate_settings() -> None:
    if not re.fullmatch(r"[1-9][0-9]?|100", REUSE_THRESHOLD):
        raise RuntimeError("RESUME_REUSE_THRESHOLD must be an integer from 1 to 100.")
    if MODE not in {"development", "production"}:
        raise RuntimeError("RESUME_ENV must be development or production.")
    if WORKSPACE_TOKEN and not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", WORKSPACE_TOKEN):
        raise RuntimeError("RESUME_WORKSPACE_TOKEN must be a 43–128 character URL-safe token.")
    if PRODUCTION:
        if not WORKSPACE_TOKEN:
            raise RuntimeError("Production requires RESUME_WORKSPACE_TOKEN.")
        if not STORAGE_SETTING or not Path(STORAGE_SETTING).is_absolute() or not DATA_DIRECTORY.is_dir():
            raise RuntimeError("Production requires RESUME_DATA_DIR pointing to an existing absolute directory.")
        if not os.access(DATA_DIRECTORY, os.R_OK | os.W_OK | os.X_OK):
            raise RuntimeError("The production data directory must be readable and writable.")
        if not os.getenv("GEMINI_API_KEY", "").strip():
            raise RuntimeError("Production requires GEMINI_API_KEY.")
        model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
        if not re.fullmatch(r"[A-Za-z0-9_-]+", model):
            raise RuntimeError("GEMINI_MODEL must be a Gemini model identifier.")
    if not FRONTEND_ORIGINS:
        raise RuntimeError("Configure at least one frontend origin.")
    for origin in FRONTEND_ORIGINS:
        try:
            parsed = urlsplit(origin)
            valid = (
                parsed.scheme in ({"https"} if PRODUCTION else {"http", "https"})
                and parsed.hostname and parsed.port != 0 and not parsed.username and not parsed.password
                and not re.search(r"[\s*]", origin) and origin == f"{parsed.scheme}://{parsed.netloc}"
            )
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError("Configure explicit frontend origins, using HTTPS in production.")
