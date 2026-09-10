"""Application settings, loaded from the environment (and .env if present).

Every setting has a working default, so the app runs even without a .env file.
Paths are resolved against the project root rather than the current working
directory, so `python -m app.main` behaves the same from anywhere.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# app/config.py -> app/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _resolve(value: str) -> Path:
    """Turn a configured path into an absolute one, relative to the project root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


# OAuth client secret downloaded from Google Cloud Console ("Desktop app" type).
CREDENTIALS_FILE = _resolve(os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"))

# Cached user token, written automatically after the first successful login.
TOKEN_FILE = _resolve(os.getenv("GOOGLE_TOKEN_FILE", "token.json"))

# Calendar to operate on. "primary" is the signed-in user's main calendar.
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# IANA timezone used when creating events.
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

# Bounds used by Week 4 free-time discovery and conflict alternatives.
WORKDAY_START_HOUR = int(os.getenv("WORKDAY_START_HOUR", "8"))
WORKDAY_END_HOUR = int(os.getenv("WORKDAY_END_HOUR", "21"))
if not 0 <= WORKDAY_START_HOUR < WORKDAY_END_HOUR <= 23:
    raise ValueError(
        "WORKDAY_START_HOUR and WORKDAY_END_HOUR must satisfy 0 <= start < end <= 23."
    )

# Live LLM provider. Keeping the model slug in configuration makes switching
# among OpenRouter models a one-line .env change.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen3-30b-a3b").strip()
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).strip()

# Local Week 5 application services.
API_HOST = os.getenv("API_HOST", "127.0.0.1").strip()
API_PORT = int(os.getenv("API_PORT", "8000"))
TASK_PILOT_API_URL = os.getenv(
    "TASK_PILOT_API_URL", f"http://{API_HOST}:{API_PORT}"
).rstrip("/")

# Structured JSON workflow logs. The directory is created lazily and ignored
# by Git because calendar queries and titles can contain private information.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper().strip()
LOG_FILE = _resolve(os.getenv("LOG_FILE", "logs/task_pilot.jsonl"))
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "2000000"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "3"))

# Full calendar access: later weeks need to update and delete events, and
# widening the scope after the fact would force every user to consent again.
SCOPES = ["https://www.googleapis.com/auth/calendar"]
