"""Everything that talks to the Google Calendar API.

This is the only module that knows about Google. Keeping it isolated means the
LangGraph agent added in a later week can wrap these functions as tools without
touching any API details.

The CRUD helpers take an authenticated ``service`` as their first argument
instead of building one themselves, so a caller authenticates once and reuses
it across many operations.
"""

from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import CALENDAR_ID, CREDENTIALS_FILE, SCOPES, TIMEZONE, TOKEN_FILE


def get_calendar_service():
    """Authenticate and return a Google Calendar API client.

    Reuses the cached token when possible, refreshes it when it has expired,
    and otherwise opens a browser for the OAuth consent screen. The resulting
    token is written back to disk so later runs need no interaction.
    """
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Google OAuth client file not found: {CREDENTIALS_FILE}\n"
                    "Download it from Google Cloud Console "
                    "(APIs & Services > Credentials > OAuth client ID, type "
                    '"Desktop app") and save it at that path.\n'
                    'See the "Google Cloud setup" section of README.md.'
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            # port=0 lets the OS pick a free port for the local redirect.
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def get_calendar_summary(service) -> str:
    """Return the human-readable name of the configured calendar."""
    calendar = service.calendars().get(calendarId=CALENDAR_ID).execute()
    return calendar.get("summary", CALENDAR_ID)


def get_events(service, max_results=10):
    """Return the next ``max_results`` events starting from now.

    Recurring events are expanded into individual occurrences and the list is
    ordered by start time, so the result reads like a real agenda.
    """
    now = datetime.now(timezone.utc).isoformat()
    response = (
        service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return response.get("items", [])


def create_event(service, summary, start, end, description=None, location=None):
    """Create a timed event and return the created event resource.

    ``start`` and ``end`` are naive local ``datetime`` objects interpreted in
    ``TIMEZONE``. The returned dict includes ``id`` and ``htmlLink``.
    """
    body = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    return service.events().insert(calendarId=CALENDAR_ID, body=body).execute()


def update_event(
    service,
    event_id,
    summary=None,
    start=None,
    end=None,
    description=None,
    location=None,
):
    """Update selected fields of an existing event and return it.

    Only the arguments you pass are changed — everything else on the event is
    left alone. This uses ``patch`` rather than ``update`` precisely so callers
    never have to send back a full event body just to move a start time.

    Raises ``ValueError`` if no fields were given, which otherwise turns into a
    confusing no-op API call.
    """
    body = {}
    if summary is not None:
        body["summary"] = summary
    if start is not None:
        body["start"] = {"dateTime": start.isoformat(), "timeZone": TIMEZONE}
    if end is not None:
        body["end"] = {"dateTime": end.isoformat(), "timeZone": TIMEZONE}
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location

    if not body:
        raise ValueError("update_event() needs at least one field to change.")

    return (
        service.events()
        .patch(calendarId=CALENDAR_ID, eventId=event_id, body=body)
        .execute()
    )


def delete_event(service, event_id):
    """Delete an event by id.

    Returns True if the event was deleted, False if it was already gone.
    Google returns 410 Gone for an event that no longer exists; treating that
    as success keeps deletion idempotent, which matters once an agent may
    retry a failed step.
    """
    try:
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    except HttpError as error:
        if error.resp.status in (404, 410):
            return False
        raise
    return True
