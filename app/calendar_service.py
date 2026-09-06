"""Everything that talks to the Google Calendar API.

This is the only module that knows about Google. Keeping it isolated lets the
LangChain tool layer wrap these functions without touching any API details.

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


def _normalise_event(event):
    """Reduce Google's large event resource to our stable public contract."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "event_id": event.get("id"),
        "title": event.get("summary", "(no title)"),
        "start": start.get("dateTime", start.get("date")),
        "end": end.get("dateTime", end.get("date")),
        "description": event.get("description"),
        "location": event.get("location"),
        "html_link": event.get("htmlLink"),
        "status": event.get("status"),
    }


def _failure(error):
    result = {
        "success": False,
        "error": str(error),
        "error_type": type(error).__name__,
    }
    status = getattr(getattr(error, "resp", None), "status", None)
    if status is not None:
        result["status_code"] = status
    return result


def _iso(value):
    """Return an RFC3339 value and reject ambiguous naive datetimes."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Calendar datetimes must be timezone-aware.")
    return value.isoformat()


def get_events(service, max_results=10, time_min=None, time_max=None):
    """Return structured upcoming events in chronological order.

    Recurring events are expanded into individual occurrences and the list is
    ordered by start time. ``time_min`` defaults to the current UTC instant.
    """
    params = {
        "calendarId": CALENDAR_ID,
        "timeMin": (
            _iso(time_min) if time_min else datetime.now(timezone.utc).isoformat()
        ),
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_max:
        params["timeMax"] = _iso(time_max)

    try:
        response = service.events().list(**params).execute()
    except HttpError as error:
        return _failure(error)

    events = [_normalise_event(item) for item in response.get("items", [])]
    return {"success": True, "count": len(events), "events": events}


def search_events(service, query, max_results=10, time_min=None, time_max=None):
    """Search events by Google's free-text query and return structured matches."""
    params = {
        "calendarId": CALENDAR_ID,
        "q": query,
        "timeMin": (
            _iso(time_min) if time_min else datetime.now(timezone.utc).isoformat()
        ),
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_max:
        params["timeMax"] = _iso(time_max)

    try:
        response = service.events().list(**params).execute()
    except HttpError as error:
        return _failure(error)

    events = [_normalise_event(item) for item in response.get("items", [])]
    return {
        "success": True,
        "query": query,
        "count": len(events),
        "events": events,
    }


def create_event(service, summary, start, end, description=None, location=None):
    """Create a timed event and return a stable structured result."""
    body = {
        "summary": summary,
        "start": {"dateTime": _iso(start), "timeZone": TIMEZONE},
        "end": {"dateTime": _iso(end), "timeZone": TIMEZONE},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    try:
        event = (
            service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        )
    except HttpError as error:
        return _failure(error)

    return {"success": True, **_normalise_event({**body, **event})}


def update_event(
    service,
    event_id,
    summary=None,
    start=None,
    end=None,
    description=None,
    location=None,
):
    """Update selected fields and return a stable structured result.

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
        body["start"] = {"dateTime": _iso(start), "timeZone": TIMEZONE}
    if end is not None:
        body["end"] = {"dateTime": _iso(end), "timeZone": TIMEZONE}
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location

    if not body:
        raise ValueError("update_event() needs at least one field to change.")

    try:
        event = (
            service.events()
            .patch(calendarId=CALENDAR_ID, eventId=event_id, body=body)
            .execute()
        )
    except HttpError as error:
        return _failure(error)

    return {"success": True, **_normalise_event(event)}


def delete_event(service, event_id):
    """Delete by id and return an idempotent structured result."""
    try:
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    except HttpError as error:
        if error.resp.status in (404, 410):
            return {
                "success": True,
                "event_id": event_id,
                "deleted": False,
                "message": "Event was already absent.",
            }
        return _failure(error)
    return {"success": True, "event_id": event_id, "deleted": True}
