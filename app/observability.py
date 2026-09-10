"""Structured, redacted workflow logging for Task Pilot."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import LOG_BACKUP_COUNT, LOG_FILE, LOG_LEVEL, LOG_MAX_BYTES


LOGGER_NAME = "task_pilot.workflow"
REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "credentials",
    "openrouter_api_key",
    "password",
    "refresh_token",
    "token",
}
_SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
)


def sanitize_log_data(value: Any, *, max_string_length: int = 2000) -> Any:
    """Recursively redact credentials and bound untrusted log field sizes."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.casefold() in _SENSITIVE_KEYS:
                cleaned[key_text] = REDACTED
            else:
                cleaned[key_text] = sanitize_log_data(
                    item, max_string_length=max_string_length
                )
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [sanitize_log_data(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, (datetime, Path)):
        return str(value)
    if isinstance(value, str):
        cleaned = value[:max_string_length]
        for pattern in _SECRET_PATTERNS:
            cleaned = pattern.sub(REDACTED, cleaned)
        return cleaned
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_string_length]


def safe_error_detail(error: Exception) -> str:
    """Return a useful error description with credentials removed."""
    return str(sanitize_log_data(str(error), max_string_length=500))


def _build_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def log_workflow(event: str, **fields: Any) -> None:
    """Append one machine-readable and secret-safe workflow event."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **sanitize_log_data(fields),
    }
    _build_logger().info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
