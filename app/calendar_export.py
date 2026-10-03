"""Calendar export: iCalendar and CSV for events, deadlines and study sessions.

ICS is written by hand rather than through a library: the subset needed here is
small and well specified, and it keeps the dependency list honest.
"""

import csv
import io
from datetime import datetime, timezone

from app.date_utils import ensure_aware

PRODUCT_ID = "-//Task Pilot//Calendar Export//EN"

# RFC 5545 limits a content line to 75 octets, continued with a leading space.
LINE_OCTETS = 73


def _escape(value):
    """Escape per RFC 5545: backslash, semicolon, comma and newline."""
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line):
    """Split a long line into RFC 5545 continuations without splitting a character."""
    raw = line.encode("utf-8")
    if len(raw) <= LINE_OCTETS + 2:
        return [line]
    pieces, start = [], 0
    limit = LINE_OCTETS
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Do not cut in the middle of a multi-byte character.
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunk = raw[start:end].decode("utf-8")
        pieces.append(chunk if not pieces else " " + chunk)
        start = end
        limit = LINE_OCTETS - 1
    return pieces


def _stamp(value):
    """Render a UTC timestamp in iCalendar's basic format."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return ensure_aware(value).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _date_stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return ensure_aware(value).strftime("%Y%m%d")


def _entry(uid, start, end, summary, description="", location="", all_day=False,
           now=None):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_stamp(now or datetime.now(timezone.utc))}"]
    if all_day:
        lines.append(f"DTSTART;VALUE=DATE:{_date_stamp(start)}")
        lines.append(f"DTEND;VALUE=DATE:{_date_stamp(end)}")
    else:
        lines.append(f"DTSTART:{_stamp(start)}")
        lines.append(f"DTEND:{_stamp(end)}")
    lines.append(f"SUMMARY:{_escape(summary)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    lines.append("END:VEVENT")
    return lines


def to_ics(events=(), assignments=(), sessions=(), *, now=None):
    """Build an iCalendar document covering everything the student schedules.

    Assignment deadlines become zero-length entries at the deadline itself,
    which is how calendar clients show a due date rather than a block of work.
    """
    lines = [
        "BEGIN:VCALENDAR",
        f"PRODID:{PRODUCT_ID}",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for event in events or []:
        start, end = event.get("start"), event.get("end")
        if not start:
            continue
        all_day = "T" not in str(start)
        lines += _entry(
            f"event-{event.get('event_id') or id(event)}@task-pilot",
            start, end or start, event.get("title") or "Untitled event",
            event.get("description", ""), event.get("location", ""),
            all_day=all_day, now=now,
        )

    for work in assignments or []:
        due = work.get("due")
        if not due:
            continue
        label = work.get("title") or "Assignment"
        detail = " · ".join(filter(None, [
            f"Subject: {work['subject']}" if work.get("subject") else "",
            f"Priority: {work['priority']}" if work.get("priority") else "",
            f"Status: {work['status']}" if work.get("status") else "",
            work.get("notes", ""),
        ]))
        lines += _entry(
            f"assignment-{work.get('id') or id(work)}@task-pilot",
            due, due, f"Due: {label}", detail, now=now,
        )

    for session in sessions or []:
        start, end = session.get("start"), session.get("end")
        if not start or not end:
            continue
        lines += _entry(
            f"study-{session.get('id') or id(session)}@task-pilot",
            start, end, session.get("title") or "Study session",
            f"Subject: {session.get('subject', 'General')}", now=now,
        )

    lines.append("END:VCALENDAR")

    folded = []
    for line in lines:
        folded.extend(_fold(line))
    # RFC 5545 requires CRLF line endings and a trailing break.
    return "\r\n".join(folded) + "\r\n"


def to_csv(events=(), assignments=(), sessions=()):
    """A flat schedule for spreadsheets: one row per scheduled thing."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["type", "title", "subject", "start", "end", "priority",
                     "status", "location"])

    for event in events or []:
        writer.writerow(["event", event.get("title", ""), "", event.get("start", ""),
                         event.get("end", ""), "", event.get("status", ""),
                         event.get("location", "")])
    for work in assignments or []:
        writer.writerow(["assignment", work.get("title", ""), work.get("subject", ""),
                         work.get("due", ""), work.get("due", ""),
                         work.get("priority", ""), work.get("status", ""), ""])
    for session in sessions or []:
        writer.writerow(["study", session.get("title", ""), session.get("subject", ""),
                         session.get("start", ""), session.get("end", ""), "",
                         session.get("status", ""), ""])
    return buffer.getvalue()
