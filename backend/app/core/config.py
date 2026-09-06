"""Application configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load variables from backend/.env if present (never committed to Git).
load_dotenv()

# Project root (repo root), derived from this file's location so paths stay
# project-relative regardless of the working directory (spec: no absolute
# filesystem paths like /home/... in application code).
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Values that mean "not configured yet" (placeholders from .env.example).
_PLACEHOLDER_VALUES = {
    "",
    "your_glm_api_key_here",
    "your_glm_model_name_here",
    "your_discord_webhook_url_here",
}

GLM_API_KEY: str = os.getenv("GLM_API_KEY", "").strip()
GLM_MODEL: str = os.getenv("GLM_MODEL", "").strip()

# GLM (Z.ai) Anthropic-compatible messages endpoint. Override to switch
# provider/proxy (e.g. https://open.bigmodel.cn/api/paas/v4 needs the
# OpenAI chat-completions format instead — see glm_service.py).
GLM_BASE_URL: str = os.getenv(
    "GLM_BASE_URL", "https://api.z.ai/api/anthropic"
).rstrip("/")

CORS_ORIGINS: list[str] = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

# --- Step 4: RAG foundation (all free & local) ---

# Local Sentence Transformers embedding model (no API key, runs offline
# after the first download).
EMBEDDING_MODEL_NAME: str = os.getenv(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
).strip()

# Persistent ChromaDB storage (project-relative, git-ignored).
CHROMA_DIR: Path = Path(
    os.getenv("CHROMA_DIR", str(PROJECT_ROOT / "data" / "chroma"))
).resolve()

# ChromaDB collection for indexed research document chunks.
CHROMA_COLLECTION_NAME: str = os.getenv(
    "CHROMA_COLLECTION_NAME", "researchflow_documents"
).strip()

# Local cache for embedding model downloads (git-ignored). HF_HOME must be
# set before huggingface_hub is imported (it is, lazily, at first model
# load) — this keeps the app portable and independent of a writable
# ~/.cache/huggingface, which may be root-owned on some machines.
MODELS_DIR: Path = PROJECT_ROOT / ".models"
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))

# --- Step 6: report generator ---

# Where generated Markdown/PDF reports are written (project-relative,
# git-ignored for *.md/*.pdf, keeps its .gitkeep).
REPORTS_DIR: Path = Path(
    os.getenv("REPORTS_DIR", str(PROJECT_ROOT / "reports"))
).resolve()


# --- Step 7: scheduler (in-process APScheduler, in-memory registry) ---

# Default IANA timezone for new schedules (explicit per request otherwise).
DEFAULT_SCHEDULE_TIMEZONE: str = os.getenv(
    "DEFAULT_SCHEDULE_TIMEZONE", "Asia/Jakarta"
).strip() or "Asia/Jakarta"


# --- Step 8: Discord notifications (optional incoming webhook) ---

# Discord incoming-webhook URL. A backend secret: it is never sent to the
# frontend, never returned by the API, never logged, and never stored
# anywhere but this process's environment. Empty/placeholder disables
# notifications (the research pipeline is unaffected either way).
# Structural validation (HTTPS + Discord hosts only) lives in
# DiscordNotificationService — the single source of truth for "configured".
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "").strip()


# --- Step 9: SQLite persistence (schedules, executions, report metadata) ---

# SQLAlchemy URL. Default is an ABSOLUTE path derived from the project root
# so the database location is stable regardless of the working directory
# (Docker compatibility comes in Step 10). The file is git-ignored; the
# connection string itself is a backend detail and is never exposed via
# the API. Tests override this with a temporary SQLite file.
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{(PROJECT_ROOT / 'data' / 'researchflow.db').as_posix()}",
).strip()


def glm_is_configured() -> bool:
    """True only when a real API key and model name are set."""
    return (
        GLM_API_KEY not in _PLACEHOLDER_VALUES
        and GLM_MODEL not in _PLACEHOLDER_VALUES
    )
