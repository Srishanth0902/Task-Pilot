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


def format_local_datetime(value: datetime | str | None) -> str:
    """Render an API datetime as a friendly, explicitly labelled IST value."""
    if value is None:
        return "Time not available"
    if isinstance(value, str) and "T" not in value:
        try:
            return datetime.fromisoformat(value).strftime("%A, %d %B %Y (all day)")
        except ValueError:
            return value

    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    local = ensure_aware(parsed)
    hour = local.strftime("%I").lstrip("0") or "0"
    return f"{local.strftime('%A, %d %B %Y')} at {hour}:{local:%M %p} IST"


def format_local_range(start: datetime | str | None, end: datetime | str | None) -> str:
    """Render a start/end pair without exposing ISO-8601 implementation details."""
    if start is None:
        return "Time not available"
    if isinstance(start, str) and "T" not in start:
        return format_local_datetime(start)

    parsed_start = datetime.fromisoformat(start) if isinstance(start, str) else start
    local_start = ensure_aware(parsed_start)
    if end is None:
        return format_local_datetime(local_start)
    parsed_end = datetime.fromisoformat(end) if isinstance(end, str) else end
    local_end = ensure_aware(parsed_end)
    start_hour = local_start.strftime("%I").lstrip("0") or "0"
    end_hour = local_end.strftime("%I").lstrip("0") or "0"
    start_clock = f"{start_hour}:{local_start:%M %p}"
    end_clock = f"{end_hour}:{local_end:%M %p}"
    if local_start.date() == local_end.date():
        return f"{local_start.strftime('%A, %d %B %Y')}, {start_clock}–{end_clock} IST"
    return f"{format_local_datetime(local_start)} to {format_local_datetime(local_end)}"


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


def infer_local_time_window(
    expression: str,
    *,
    now: datetime | None = None,
    timezone_name: str = TIMEZONE,
    start_hour: int = 8,
    end_hour: int = 21,
) -> tuple[datetime, datetime] | None:
    """Infer a local day/range for availability requests.

    This deterministic safety net supports phrases such as ``tomorrow after
    6 PM``, ``Monday before 2 PM``, and ``Friday between 10 AM and 1 PM``.
    If no day can be identified, ``None`` is returned so the conversation can
    ask a useful clarification.
    """
    if not isinstance(expression, str) or not expression.strip():
        return None

    text = " ".join(expression.strip().lower().split())
    reference = ensure_aware(now, timezone_name) if now else local_now(timezone_name)
    zone = get_timezone(timezone_name)

    if re.search(r"\btomorrow\b", text):
        target_date = (reference + timedelta(days=1)).date()
    elif re.search(r"\btoday\b", text):
        target_date = reference.date()
    else:
        iso_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        weekday = re.search(
            r"\b(?P<next>next\s+)?(?P<weekday>" + "|".join(WEEKDAYS) + r")\b",
            text,
        )
        if iso_date:
            target_date = datetime.fromisoformat(iso_date.group(1)).date()
        elif weekday:
            target_weekday = WEEKDAYS[weekday.group("weekday")]
            days = (target_weekday - reference.weekday()) % 7
            if weekday.group("next") and days == 0:
                days = 7
            target_date = (reference + timedelta(days=days)).date()
        else:
            return None

    window_start = datetime.combine(target_date, time(start_hour), tzinfo=zone)
    window_end = datetime.combine(target_date, time(end_hour), tzinfo=zone)
    clock_token = r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?"

    between = re.search(
        rf"\bbetween\s+(?P<start>{clock_token})\s+and\s+(?P<end>{clock_token})",
        text,
        re.IGNORECASE,
    )
    if between:
        start_clock = _parse_clock(between.group("start"), time(start_hour))
        end_clock = _parse_clock(between.group("end"), time(end_hour))
        window_start = datetime.combine(target_date, start_clock, tzinfo=zone)
        window_end = datetime.combine(target_date, end_clock, tzinfo=zone)
    else:
        lower_bound = re.search(
            rf"\b(?:after|from|at)\s+(?P<clock>{clock_token})",
            text,
            re.IGNORECASE,
        )
        upper_bound = re.search(
            rf"\b(?:before|until)\s+(?P<clock>{clock_token})",
            text,
            re.IGNORECASE,
        )
        if lower_bound:
            clock = _parse_clock(lower_bound.group("clock"), time(start_hour))
            window_start = datetime.combine(target_date, clock, tzinfo=zone)
        if upper_bound:
            clock = _parse_clock(upper_bound.group("clock"), time(end_hour))
            window_end = datetime.combine(target_date, clock, tzinfo=zone)

    if window_end <= window_start:
        raise ValueError("The requested availability range ends before it starts.")
    return window_start, window_end


def coerce_datetime(value: datetime | str) -> datetime:
    """Pydantic validator helper accepting aware datetimes or date phrases."""
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, str):
        return parse_datetime_expression(value)
    raise TypeError("Expected a datetime or supported date expression.")
