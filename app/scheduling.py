"""Pure scheduling helpers for Week 4 planning and conflict detection."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.config import TIMEZONE, WORKDAY_END_HOUR, WORKDAY_START_HOUR
from app.date_utils import ensure_aware, get_timezone


def event_interval(event: dict) -> tuple[datetime, datetime] | None:
    """Return an interval, including Google's exclusive all-day end date."""
    start = event.get("start")
    end = event.get("end")
    if not start or not end:
        return None
    return ensure_aware(datetime.fromisoformat(start)), ensure_aware(
        datetime.fromisoformat(end)
    )


def overlapping_events(
    events: list[dict],
    start: datetime,
    end: datetime,
    *,
    exclude_event_ids: set[str] | None = None,
) -> list[dict]:
    """Return events intersecting ``[start, end)`` using half-open intervals."""
    excluded = exclude_event_ids or set()
    matches = []
    for event in events:
        if event.get("event_id") in excluded:
            continue
        if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
            continue
        interval = event_interval(event)
        if interval and interval[0] < end and start < interval[1]:
            matches.append(event)
    return matches


def day_window(value: date | datetime) -> tuple[datetime, datetime]:
    """Return local midnight boundaries for the date containing ``value``."""
    target = value.date() if isinstance(value, datetime) else value
    zone = get_timezone(TIMEZONE)
    start = datetime.combine(target, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def working_window(
    value: date | datetime,
    *,
    start_hour: int = WORKDAY_START_HOUR,
    end_hour: int = WORKDAY_END_HOUR,
) -> tuple[datetime, datetime]:
    """Return the default local scheduling window for a date."""
    target = value.date() if isinstance(value, datetime) else value
    zone = get_timezone(TIMEZONE)
    return (
        datetime.combine(target, time(start_hour), tzinfo=zone),
        datetime.combine(target, time(end_hour), tzinfo=zone),
    )


def find_free_slots(
    events: list[dict],
    window_start: datetime,
    window_end: datetime,
    duration: timedelta,
    *,
    limit: int = 3,
    exclude_event_ids: set[str] | None = None,
) -> list[dict]:
    """Return the earliest gaps of ``duration`` inside a scheduling window."""
    if duration <= timedelta(0):
        raise ValueError("Free-slot duration must be positive.")
    if window_end <= window_start:
        raise ValueError("Free-slot window end must be after its start.")

    busy = []
    for event in events:
        if exclude_event_ids and event.get("event_id") in exclude_event_ids:
            continue
        if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
            continue
        interval = event_interval(event)
        if not interval:
            continue
        start = max(interval[0], window_start)
        end = min(interval[1], window_end)
        if start < end:
            busy.append((start, end))
    busy.sort(key=lambda item: item[0])

    merged: list[list[datetime]] = []
    for start, end in busy:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    slots = []

    def add_gap(gap_start: datetime, gap_end: datetime):
        cursor = gap_start
        while cursor + duration <= gap_end and len(slots) < limit:
            slots.append(
                {"start": cursor.isoformat(), "end": (cursor + duration).isoformat()}
            )
            cursor += duration

    cursor = window_start
    for start, end in merged:
        if start - cursor >= duration:
            add_gap(cursor, start)
            if len(slots) >= limit:
                return slots
        cursor = max(cursor, end)
    if window_end - cursor >= duration and len(slots) < limit:
        add_gap(cursor, window_end)
    return slots


def build_bulk_changes(events: list[dict], action: dict) -> list[dict]:
    """Create deterministic update/delete proposals without mutating a calendar."""
    intent = action.get("intent")
    if intent == "bulk_delete":
        return [
            {
                "action": "delete",
                "event_id": event.get("event_id"),
                "title": event.get("title"),
                "old_start": event.get("start"),
                "old_end": event.get("end"),
            }
            for event in events
            if event.get("event_id")
        ]

    if intent != "bulk_update":
        raise ValueError("Bulk planning supports only update and delete.")

    shift_minutes = action.get("shift_minutes")
    target_date = action.get("target_date")
    if shift_minutes is None and not target_date:
        raise ValueError("Bulk update needs shift_minutes or target_date.")

    changes = []
    for event in events:
        # Bulk time shifts cannot silently turn an all-day event into a timed one.
        if "T" not in (event.get("start") or ""):
            continue
        interval = event_interval(event)
        if not interval or not event.get("event_id"):
            continue
        old_start, old_end = interval
        if shift_minutes is not None:
            delta = timedelta(minutes=shift_minutes)
            new_start, new_end = old_start + delta, old_end + delta
        else:
            new_date = date.fromisoformat(target_date)
            new_start = datetime.combine(
                new_date, old_start.timetz(), tzinfo=get_timezone(TIMEZONE)
            )
            new_end = new_start + (old_end - old_start)
        changes.append(
            {
                "action": "update",
                "event_id": event["event_id"],
                "title": event.get("title"),
                "old_start": old_start.isoformat(),
                "old_end": old_end.isoformat(),
                "new_start": new_start.isoformat(),
                "new_end": new_end.isoformat(),
            }
        )
    return changes
