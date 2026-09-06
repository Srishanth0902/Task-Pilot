"""Week 1 demo: prove the Google Calendar integration works end to end.

Runs through every operation the Week 1 sprint asks for:

  Task 3  get_events()     read upcoming events
  Task 4  create_event()   "Agentic AI Project Work", tomorrow 6 PM, 1 hour
  Task 5  update_event()   and delete_event()

Run it with:

    python -m app.main

The first run opens a browser for the OAuth consent screen; later runs reuse
the cached token and need no interaction.
"""

import sys
from datetime import datetime, timedelta

from googleapiclient.errors import HttpError

from app.calendar_service import (
    create_event,
    delete_event,
    get_calendar_service,
    get_calendar_summary,
    get_events,
    update_event,
)
from app.config import CALENDAR_ID, TIMEZONE
from app.date_utils import local_now

# Task 4's test case, verbatim from the sprint brief.
PROJECT_EVENT = "Agentic AI Project Work"

# Task 5 exercises update and delete on a throwaway event, so the event
# created for Task 4 stays on the calendar for you to verify by eye.
TEMP_EVENT = "Task-Pilot Temp Event"


def _tomorrow_at(hour):
    """Return a timezone-aware local time tomorrow, on the minute."""
    return (local_now() + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _start_of(event):
    """Render an event's start time as 'YYYY-MM-DD HH:MM'.

    All-day events carry a plain 'date' instead of a 'dateTime', so they are
    labelled rather than given a fake midnight time.
    """
    start = event.get("start", {})
    if isinstance(start, str):
        if "T" not in start:
            return f"{start} (all day)"
        return datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M")
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"]).strftime("%Y-%m-%d %H:%M")
    if "date" in start:
        return f"{start['date']} (all day)"
    return "unknown"


def show_events(service, max_results=10):
    """Print upcoming events in the sprint's expected format."""
    result = get_events(service, max_results=max_results)
    if not result["success"]:
        raise RuntimeError(result["error"])
    events = result["events"]

    print("Upcoming Events:")
    print()
    if not events:
        print("  (none)")
        print()
        return events

    for index, event in enumerate(events, start=1):
        print(f"{index}. {event.get('title', '(no title)')}")
        print(f"   {_start_of(event)}")
        print()
    return events


def section(title):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def run():
    section("Connecting to Google Calendar")
    service = get_calendar_service()
    print(f"Connected to calendar: {get_calendar_summary(service)}")
    print(f"Calendar ID: {CALENDAR_ID}   Timezone: {TIMEZONE}")

    # ---- Task 3: read events -------------------------------------------
    section("Task 3 - get_events()")
    show_events(service)

    # ---- Task 4: create an event ---------------------------------------
    section("Task 4 - create_event()")
    start = _tomorrow_at(18)
    end = start + timedelta(hours=1)
    print(f"Creating '{PROJECT_EVENT}'")
    print(f"  {start:%a %d %b %Y, %I:%M %p} - {end:%I:%M %p} (1 hour)")

    event = create_event(
        service,
        summary=PROJECT_EVENT,
        start=start,
        end=end,
        description="Created by the Task-Pilot Week 1 sprint demo.",
    )
    if not event["success"]:
        raise RuntimeError(event["error"])
    print(f"Created (id: {event['event_id']})")
    print(f"  {event.get('html_link', '')}")

    print("\nRe-reading the calendar to confirm it is really there:")
    reread = get_events(service, max_results=10)
    if not reread["success"]:
        raise RuntimeError(reread["error"])
    summaries = [item.get("title") for item in reread.get("events", [])]
    if PROJECT_EVENT in summaries:
        print(f"  Confirmed: '{PROJECT_EVENT}' is on the calendar.")
    else:
        # Not fatal: it can fall outside the window if the calendar is busy.
        print(
            f"  Note: '{PROJECT_EVENT}' was created but is not in the next "
            "10 events. Open the link above to check it directly."
        )

    # ---- Task 5: update and delete -------------------------------------
    section("Task 5 - update_event() and delete_event()")
    temp_start = _tomorrow_at(21)
    temp = create_event(
        service,
        summary=TEMP_EVENT,
        start=temp_start,
        end=temp_start + timedelta(minutes=30),
    )
    if not temp["success"]:
        raise RuntimeError(temp["error"])
    print(f"Created a throwaway event to modify (id: {temp['event_id']})")
    print(f"  '{TEMP_EVENT}' at {temp_start:%Y-%m-%d %H:%M}")

    moved_start = _tomorrow_at(22)
    updated = update_event(
        service,
        temp["event_id"],
        summary="Task-Pilot Temp Event (renamed)",
        start=moved_start,
        end=moved_start + timedelta(minutes=30),
    )
    print("\nupdate_event() -> renamed and moved:")
    if not updated["success"]:
        raise RuntimeError(updated["error"])
    print(f"  '{updated.get('title')}' at {_start_of(updated)}")

    print("\ndelete_event() -> removing it again:")
    deleted = delete_event(service, temp["event_id"])
    deleted_again = delete_event(service, temp["event_id"])
    if not deleted["success"]:
        raise RuntimeError(deleted["error"])
    if not deleted_again["success"]:
        raise RuntimeError(deleted_again["error"])
    print(f"  deleted: {deleted.get('deleted')}")
    print(f"  deleting again (already gone): {deleted_again.get('deleted')}")

    section("Done")
    print("All Week 1 operations succeeded: read, create, update, delete.")
    print(f"'{PROJECT_EVENT}' was left on your calendar - go and look at it.")


def main():
    try:
        run()
    except FileNotFoundError as error:
        print(f"\nSetup incomplete:\n{error}", file=sys.stderr)
        return 1
    except HttpError as error:
        print(f"\nGoogle Calendar API returned an error:\n{error}", file=sys.stderr)
        print(
            "\nCommon causes: the Google Calendar API is not enabled for your "
            "project, or the calendar ID in .env does not exist.\n"
            'See the "Troubleshooting" section of README.md.',
            file=sys.stderr,
        )
        return 1
    except RuntimeError as error:
        print(f"\nCalendar operation failed:\n{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
