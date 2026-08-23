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

# Full calendar access: later weeks need to update and delete events, and
# widening the scope after the fact would force every user to consent again.
SCOPES = ["https://www.googleapis.com/auth/calendar"]
