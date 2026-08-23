"""Everything that talks to the Google Calendar API.

This is the only module that knows about Google. Keeping it isolated means the
LangGraph agent added in a later week can wrap these functions as tools without
touching any API details.

The read/write helpers take an authenticated ``service`` as their first
argument instead of building one themselves, so a caller authenticates once and
reuses it for many operations.
"""

from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

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


def list_upcoming_events(service, max_results=5):
    """Return the next ``max_results`` events starting from now."""
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
