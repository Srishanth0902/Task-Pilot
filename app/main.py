"""Week 1 smoke test: connect to Google Calendar and create one event.

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
    get_calendar_service,
    get_calendar_summary,
    list_upcoming_events,
)
from app.config import CALENDAR_ID, TIMEZONE

EVENT_TITLE = "Task-Pilot Test Event"
EVENT_DESCRIPTION = "Created automatically by the Task-Pilot Week 1 smoke test."


def _tomorrow_at(hour):
    """Return tomorrow at the given hour, on the minute."""
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.replace(hour=hour, minute=0, second=0, microsecond=0)


def _format_start(event):
    """Render an event's start time, whether it is timed or all-day."""
    start = event["start"]
    return start.get("dateTime", start.get("date", "unknown"))


def run():
    print("Connecting to Google Calendar...")
    service = get_calendar_service()
    print(f"Connected to calendar: {get_calendar_summary(service)}")
    print(f"Calendar ID: {CALENDAR_ID}   Timezone: {TIMEZONE}\n")

    start = _tomorrow_at(10)
    end = start + timedelta(hours=1)
    print(f"Creating event '{EVENT_TITLE}'")
    print(f"  {start:%a %d %b %Y, %I:%M %p} - {end:%I:%M %p}")

    event = create_event(
        service,
        summary=EVENT_TITLE,
        start=start,
        end=end,
        description=EVENT_DESCRIPTION,
    )
    print(f"Event created (id: {event['id']})")
    print(f"  {event.get('htmlLink', '')}\n")

    events = list_upcoming_events(service, max_results=5)
    if not events:
        print("No upcoming events found.")
    else:
        print(f"Next {len(events)} upcoming event(s):")
        for item in events:
            print(f"  {_format_start(item):<25} {item.get('summary', '(no title)')}")

    print("\nDone. Week 1 objective met: connected and created an event.")


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
