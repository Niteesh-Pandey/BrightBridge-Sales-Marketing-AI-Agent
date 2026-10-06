"""Central configuration. All secrets come from environment variables only."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_NAME = "BrightBridge Sales Intelligence"
APP_VERSION = "0.1.3"
COMPANY_NAME = "BrightBridge Solutions"
TAGLINE = "Helping growing businesses turn repetitive work into smarter workflows."
OWNER = "Niteesh Pandey"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEMO_DIR = DATA_DIR / "demo"
IMPORT_DIR = DATA_DIR / "imports"
EXPORT_DIR = DATA_DIR / "exports"

DATA_MODE = os.getenv("DATA_MODE", "demo").strip().lower()
if DATA_MODE not in ("demo", "real"):
    DATA_MODE = "demo"


def default_database_url(mode: str | None = None) -> str:
    """Resolve the database URL. DATABASE_URL wins; otherwise one SQLite file per mode."""
    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        return explicit
    mode = mode or DATA_MODE
    return f"sqlite:///{(DATA_DIR / f'brightbridge_{mode}.db').as_posix()}"


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

# Import safety limits
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "5"))
MAX_IMPORT_ROWS = int(os.getenv("MAX_IMPORT_ROWS", "20000"))
ALLOWED_UPLOAD_EXTENSIONS = (".csv", ".xlsx")

DEMO_BANNER = "DEMO MODE — Synthetic Business Data"
GEMINI_MISSING_MSG = "Gemini AI is not configured. CRM analytics remain available."


def gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def current_gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def current_gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL


def gemini_base_url() -> str:
    """Optional override (used by tests with a local fake server). Empty = Google's endpoint."""
    return os.getenv("GEMINI_API_BASE_URL", "").strip()
