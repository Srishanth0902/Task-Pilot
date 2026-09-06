"""Timezone-safe parsing for the date phrases required in Week 2."""

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import TIMEZONE


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

TIME_PATTERN = re.compile(
    r"(?:\bat\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
    r"(?P<period>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)


def get_timezone(name: str = TIMEZONE) -> ZoneInfo:
    """Return an IANA timezone with a useful configuration error."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown IANA timezone: {name}") from error


def ensure_aware(value: datetime, timezone_name: str = TIMEZONE) -> datetime:
    """Attach the configured timezone to naive values; convert aware values."""
    zone = get_timezone(timezone_name)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def local_now(timezone_name: str = TIMEZONE) -> datetime:
    return datetime.now(get_timezone(timezone_name))


def _parse_clock(expression: str, default: time) -> time:
    match = TIME_PATTERN.search(expression)
    if not match:
        return default

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    period = (match.group("period") or "").lower().replace(".", "")

    if minute > 59:
        raise ValueError(f"Invalid minute in date expression: {expression!r}")
    if period:
        if not 1 <= hour <= 12:
            raise ValueError(f"Invalid 12-hour time in date expression: {expression!r}")
        hour = hour % 12 + (12 if period == "pm" else 0)
    elif hour > 23:
        raise ValueError(f"Invalid hour in date expression: {expression!r}")

    return time(hour, minute)


def _at_clock(value: datetime, clock: time) -> datetime:
    return value.replace(
        hour=clock.hour, minute=clock.minute, second=0, microsecond=0
    )


def parse_datetime_expression(
    expression: str,
    *,
    now: datetime | None = None,
    timezone_name: str = TIMEZONE,
    default_time: time = time(9, 0),
) -> datetime:
    """Parse the relative expressions promised by the Week 2 brief.

    Supported forms include ``tomorrow at 6 PM``, ``next Monday``,
    ``Friday at 18:00``, ``in two hours``, ``next week``, and ISO-8601.
    Every result is timezone-aware in ``timezone_name``.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Date expression cannot be empty.")

    text = " ".join(expression.strip().lower().split())
    reference = ensure_aware(now, timezone_name) if now else local_now(timezone_name)

    # ISO-8601 input is accepted directly, including the output of an LLM.
    try:
        return ensure_aware(datetime.fromisoformat(expression.strip()), timezone_name)
    except ValueError:
        pass

    relative = re.fullmatch(
        r"in\s+(?P<amount>\d+|" + "|".join(NUMBER_WORDS) + r")\s+hours?",
        text,
    )
    if relative:
        token = relative.group("amount")
        hours = int(token) if token.isdigit() else NUMBER_WORDS[token]
        return reference + timedelta(hours=hours)

    clock = _parse_clock(text, default_time)

    if text.startswith("tomorrow"):
        return _at_clock(reference + timedelta(days=1), clock)

    if text.startswith("next week"):
        result = reference + timedelta(days=7)
        return _at_clock(result, clock) if TIME_PATTERN.search(text) else result

    weekday_match = re.match(
        r"(?P<next>next\s+)?(?P<weekday>" + "|".join(WEEKDAYS) + r")\b",
        text,
    )
    if weekday_match:
        target = WEEKDAYS[weekday_match.group("weekday")]
        days = (target - reference.weekday()) % 7
        if weekday_match.group("next") and days == 0:
            days = 7
        candidate = _at_clock(reference + timedelta(days=days), clock)
        # "Friday" means the next future occurrence, not a time already passed.
        if not weekday_match.group("next") and candidate <= reference:
            candidate += timedelta(days=7)
        return candidate

    raise ValueError(
        "Unsupported date expression. Use ISO-8601, 'tomorrow at 6 PM', "
        "'next Monday', 'Friday at 6 PM', 'in two hours', or 'next week'."
    )


def coerce_datetime(value: datetime | str) -> datetime:
    """Pydantic validator helper accepting aware datetimes or date phrases."""
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, str):
        return parse_datetime_expression(value)
    raise TypeError("Expected a datetime or supported date expression.")
