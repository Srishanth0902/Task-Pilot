"""Smart Study Planner: turn assignments into dated study sessions.

The planner is a pure function over assignments, existing calendar events and
the account's scheduling preferences. Keeping it free of I/O is what makes the
packing rules testable without a calendar or a model behind them.

Scheduling is deliberately greedy rather than optimal: assignments are taken in
urgency order and each one claims the earliest slots that still fit before its
deadline. A student can follow that rule in their head, which matters more here
than squeezing out a perfect packing.
"""

from datetime import timedelta

from app.assignments import Assignment
from app.date_utils import ensure_aware, local_now
from app.scheduling import find_free_slots

# Session lengths. Long enough to get somewhere, short enough to stay honest
# about attention spans, and a tail shorter than MIN_SESSION is folded into the
# previous session rather than scheduled on its own.
MAX_SESSION_MINUTES = 90
MIN_SESSION_MINUTES = 30

# Guard rails so one enormous assignment cannot fill a student's entire month.
MAX_SESSIONS_PER_ASSIGNMENT = 12
MAX_SESSIONS_PER_DAY = 4
DEFAULT_HORIZON_DAYS = 7


def split_effort(minutes: int) -> list[int]:
    """Break total effort into session lengths.

    A remainder below MIN_SESSION_MINUTES is added to the last full session
    instead of becoming a stub, so 100 minutes is one 90 and one 30 rather than
    a 90 and a 10.
    """
    if minutes <= MAX_SESSION_MINUTES:
        return [max(minutes, MIN_SESSION_MINUTES)]
    sessions = []
    remaining = minutes
    while remaining > 0 and len(sessions) < MAX_SESSIONS_PER_ASSIGNMENT:
        if remaining <= MAX_SESSION_MINUTES:
            if remaining < MIN_SESSION_MINUTES and sessions:
                sessions[-1] += remaining
            else:
                sessions.append(remaining)
            break
        sessions.append(MAX_SESSION_MINUTES)
        remaining -= MAX_SESSION_MINUTES
    return sessions


def _as_busy(start, end):
    """Shape an allocated slot like a calendar event for the free-slot search."""
    return {"event_id": None, "start": start, "end": end, "status": "confirmed"}


def plan_sessions(
    assignments,
    events,
    preferences=None,
    *,
    days=DEFAULT_HORIZON_DAYS,
    now=None,
    completed_minutes=None,
):
    """Propose study sessions for unfinished assignments.

    ``completed_minutes`` maps assignment id to minutes already studied, so
    re-planning after some work is done schedules only what is left.
    """
    from app.preferences import SchedulingPreferences

    preferences = preferences or SchedulingPreferences()
    if isinstance(preferences, dict):
        preferences = SchedulingPreferences.model_validate(preferences)
    completed_minutes = completed_minutes or {}
    moment = now or local_now()

    pending = []
    for record in assignments:
        work = record if isinstance(record, Assignment) else Assignment.model_validate(record)
        if work.done:
            continue
        identity = record.get("id") if isinstance(record, dict) else None
        remaining = work.estimated_minutes - completed_minutes.get(identity, 0)
        if remaining < MIN_SESSION_MINUTES:
            continue
        pending.append((work.urgency(moment), identity, work, remaining))

    # Most pressing first; the deadline is the tie-breaker so two equally
    # urgent assignments still resolve deterministically.
    pending.sort(key=lambda item: (-item[0], ensure_aware(item[2].due)))

    busy = list(events)
    per_day = {}
    planned = []

    for _, identity, work, remaining in pending:
        deadline = ensure_aware(work.due)
        for minutes in split_effort(remaining):
            duration = timedelta(minutes=minutes)
            placed = False
            for offset in range(days + 1):
                day = (moment + timedelta(days=offset)).date()
                if per_day.get(day, 0) >= MAX_SESSIONS_PER_DAY:
                    continue
                window_start, window_end = preferences.window(day, work.title)
                # Never schedule in the past, and never past the deadline.
                window_start = max(window_start, moment)
                window_end = min(window_end, deadline)
                if window_end - window_start < duration:
                    continue
                slots = find_free_slots(
                    preferences.busy_events(busy), window_start, window_end, duration, limit=1
                )
                if not slots:
                    continue
                slot = slots[0]
                planned.append({
                    "assignment_id": identity,
                    "title": f"Study: {work.title}",
                    "subject": work.subject or "General",
                    "start": slot["start"],
                    "end": slot["end"],
                    "minutes": minutes,
                    "priority": work.priority,
                    "due": deadline.isoformat(),
                })
                busy.append(_as_busy(slot["start"], slot["end"]))
                per_day[day] = per_day.get(day, 0) + 1
                placed = True
                break
            if not placed:
                # No room before this deadline; later sessions for the same
                # assignment will not fit either, so stop trying.
                break

    planned.sort(key=lambda session: session["start"])
    return planned


def unschedulable(assignments, planned, *, completed_minutes=None, now=None):
    """Assignments with work left that the planner could not place in time.

    Surfacing these is the point: silently dropping an assignment that does not
    fit before its deadline is exactly the failure a planner must not hide.
    """
    completed_minutes = completed_minutes or {}
    moment = now or local_now()
    scheduled = {}
    for session in planned:
        scheduled[session["assignment_id"]] = (
            scheduled.get(session["assignment_id"], 0) + session["minutes"]
        )
    stranded = []
    for record in assignments:
        work = record if isinstance(record, Assignment) else Assignment.model_validate(record)
        if work.done:
            continue
        identity = record.get("id") if isinstance(record, dict) else None
        remaining = (
            work.estimated_minutes
            - completed_minutes.get(identity, 0)
            - scheduled.get(identity, 0)
        )
        if remaining >= MIN_SESSION_MINUTES:
            stranded.append({
                "assignment_id": identity,
                "title": work.title,
                "subject": work.subject or "General",
                "due": ensure_aware(work.due).isoformat(),
                "unscheduled_minutes": remaining,
                "overdue": work.overdue(moment),
            })
    return stranded


def progress(assignments, sessions):
    """Per-subject and overall progress, from recorded study sessions."""
    subjects = {}
    for record in assignments:
        work = record if isinstance(record, Assignment) else Assignment.model_validate(record)
        name = work.subject or "General"
        bucket = subjects.setdefault(name, {
            "subject": name, "assignments": 0, "completed_assignments": 0,
            "planned_minutes": 0, "studied_minutes": 0,
        })
        bucket["assignments"] += 1
        if work.done:
            bucket["completed_assignments"] += 1

    for session in sessions:
        name = session.get("subject") or "General"
        bucket = subjects.setdefault(name, {
            "subject": name, "assignments": 0, "completed_assignments": 0,
            "planned_minutes": 0, "studied_minutes": 0,
        })
        minutes = session.get("minutes", 0)
        bucket["planned_minutes"] += minutes
        if session.get("status") == "done":
            bucket["studied_minutes"] += minutes

    rows = []
    for bucket in subjects.values():
        planned = bucket["planned_minutes"]
        bucket["completion"] = (
            round(bucket["studied_minutes"] / planned * 100, 1) if planned else 0.0
        )
        rows.append(bucket)
    rows.sort(key=lambda row: row["subject"].casefold())

    total_planned = sum(row["planned_minutes"] for row in rows)
    total_studied = sum(row["studied_minutes"] for row in rows)
    return {
        "subjects": rows,
        "planned_minutes": total_planned,
        "studied_minutes": total_studied,
        "completion": round(total_studied / total_planned * 100, 1) if total_planned else 0.0,
        "assignments": sum(row["assignments"] for row in rows),
        "completed_assignments": sum(row["completed_assignments"] for row in rows),
    }
