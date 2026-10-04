"""One calendar interface, two backends.

Task Pilot can run against Google Calendar or against its own database. Both
are reached through the same small interface so the agent, the tools, undo and
the HTTP routes contain no per-provider branching: swapping the provider swaps
the calendar, and nothing else changes.

Every method returns the structured shape the agent and the frontend already
expect, so a native event and a Google event are interchangeable to callers.
Each event carries a ``provider`` field, and that field is what keeps a
confirmation from landing on the wrong calendar after someone switches.
"""

from datetime import datetime, timezone

from app.calendar_service import (
    create_event,
    delete_event,
    get_events,
    search_events,
    update_event,
)
from app.date_utils import ensure_aware
from app.scheduling import event_interval

GOOGLE = "google"
NATIVE = "native"

# Shown wherever a calendar has to be named for a person.
PROVIDER_LABELS = {GOOGLE: "Google Calendar", NATIVE: "Task Pilot calendar"}


class CalendarProvider:
    """The operations the agent needs from a calendar.

    Subclasses return dictionaries in the shared contract: a failure is
    ``{"success": False, "error": ...}``, a read is ``{"success": True,
    "count": n, "events": [...]}``, and a write echoes the affected event.
    """

    name = ""

    @property
    def label(self):
        return PROVIDER_LABELS.get(self.name, self.name)

    def list_events(self, max_results=10, time_min=None, time_max=None):
        raise NotImplementedError

    def search_events(self, query, max_results=10, time_min=None, time_max=None):
        raise NotImplementedError

    def create_event(self, summary, start, end, description=None, location=None):
        raise NotImplementedError

    def update_event(self, event_id, summary=None, start=None, end=None,
                     description=None, location=None, if_match=None):
        raise NotImplementedError

    def delete_event(self, event_id, if_match=None):
        raise NotImplementedError

    def get_event(self, event_id):
        """Return one event, or None when it is absent or cancelled."""
        raise NotImplementedError

    def close(self):
        """Release transport resources. A no-op for providers that hold none."""


def _stamp(result, provider_name):
    """Tag a result and the events inside it with the provider that produced it."""
    if not isinstance(result, dict):
        return result
    result["provider"] = provider_name
    if isinstance(result.get("events"), list):
        for event in result["events"]:
            if isinstance(event, dict):
                event.setdefault("provider", provider_name)
    return result


class GoogleCalendarProvider(CalendarProvider):
    """Google Calendar, reached through the existing authenticated service."""

    name = GOOGLE

    def __init__(self, service):
        self.service = service

    def list_events(self, max_results=10, time_min=None, time_max=None):
        return _stamp(get_events(self.service, max_results, time_min, time_max), self.name)

    def search_events(self, query, max_results=10, time_min=None, time_max=None):
        return _stamp(
            search_events(self.service, query, max_results, time_min, time_max), self.name
        )

    def create_event(self, summary, start, end, description=None, location=None):
        return _stamp(
            create_event(self.service, summary, start, end, description, location), self.name
        )

    def update_event(self, event_id, summary=None, start=None, end=None,
                     description=None, location=None, if_match=None):
        # Google's conditional write is an If-Match header on the request,
        # which the existing helper already applies when given an etag.
        return _stamp(
            update_event(
                self.service, event_id=event_id, summary=summary, start=start, end=end,
                description=description, location=location, if_match=if_match,
            ),
            self.name,
        )

    def delete_event(self, event_id, if_match=None):
        return _stamp(delete_event(self.service, event_id, if_match=if_match), self.name)

    def get_event(self, event_id):
        from googleapiclient.errors import HttpError

        from app.calendar_service import _normalise_event
        from app.config import CALENDAR_ID

        try:
            resource = (
                self.service.events()
                .get(calendarId=CALENDAR_ID, eventId=event_id)
                .execute()
            )
        except HttpError as error:
            if getattr(error, "resp", None) is not None and error.resp.status in (404, 410):
                return None
            raise
        event = _normalise_event(resource)
        if event.get("status") == "cancelled":
            return None
        event["provider"] = self.name
        return event

    def close(self):
        if hasattr(self.service, "close"):
            self.service.close()


class NativeCalendarProvider(CalendarProvider):
    """Task Pilot's own calendar, stored in the application database.

    This provider never touches Google: no credentials, no network. It is what
    makes the app usable before — or instead of — connecting an account.
    """

    name = NATIVE

    def __init__(self, store, user_id):
        self.store = store
        self.user_id = user_id

    def _ok(self, event):
        return {"success": True, "provider": self.name, **event}

    def list_events(self, max_results=10, time_min=None, time_max=None):
        try:
            events = self.store.native_events(
                self.user_id,
                time_min=ensure_aware(time_min) if time_min else self._now(),
                time_max=ensure_aware(time_max) if time_max else None,
                max_results=max_results,
            )
        except Exception as error:  # pragma: no cover - storage faults
            return {"success": False, "error": str(error), "provider": self.name}
        return {"success": True, "count": len(events), "events": events,
                "provider": self.name}

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def search_events(self, query, max_results=10, time_min=None, time_max=None):
        """Case-insensitive substring match over title, description and location.

        Google's own search is substring-ish rather than semantic, so matching
        the same way keeps agent behaviour consistent across providers.
        """
        needle = (query or "").strip().casefold()
        events = self.store.native_events(
            self.user_id,
            time_min=ensure_aware(time_min) if time_min else None,
            time_max=ensure_aware(time_max) if time_max else None,
            max_results=500,
        )
        if needle:
            events = [
                event for event in events
                if needle in " ".join(
                    str(event.get(field) or "")
                    for field in ("title", "description", "location")
                ).casefold()
            ]
        events = events[:max_results]
        return {"success": True, "count": len(events), "events": events,
                "query": query, "provider": self.name}

    def create_event(self, summary, start, end, description=None, location=None):
        if not summary or not str(summary).strip():
            return {"success": False, "error": "An event needs a title.",
                    "provider": self.name}
        start, end = ensure_aware(start), ensure_aware(end)
        if end <= start:
            return {"success": False, "error": "An event must end after it starts.",
                    "provider": self.name}
        event = self.store.create_native_event(
            self.user_id, title=str(summary).strip(), start=start, end=end,
            description=description, location=location,
        )
        return self._ok(event)

    def update_event(self, event_id, summary=None, start=None, end=None,
                     description=None, location=None, if_match=None):
        changes = {
            "title": summary, "description": description, "location": location,
            "start": ensure_aware(start) if start else None,
            "end": ensure_aware(end) if end else None,
        }
        if all(value is None for value in changes.values()):
            return {"success": False, "error": "Nothing to change on that event.",
                    "provider": self.name}
        try:
            event = self.store.update_native_event(
                self.user_id, event_id, changes, if_match=if_match
            )
        except PermissionError as error:
            return {"success": False, "error": str(error), "provider": self.name}
        except ValueError as error:
            return {"success": False, "error": str(error), "provider": self.name}
        if event is None:
            return {"success": False, "error": "That event no longer exists.",
                    "status_code": 404, "provider": self.name}
        return self._ok(event)

    def delete_event(self, event_id, if_match=None):
        try:
            removed = self.store.delete_native_event(
                self.user_id, event_id, if_match=if_match
            )
        except PermissionError as error:
            return {"success": False, "error": str(error), "provider": self.name}
        # Deleting something already gone is a success, matching the Google
        # path, so a repeated confirmation is not reported as a failure.
        return {"success": True, "event_id": event_id, "deleted": removed,
                "provider": self.name}

    def get_event(self, event_id):
        return self.store.native_event(self.user_id, event_id)


def busy_intervals(provider, start, end, *, exclude_event_ids=None):
    """Occupied intervals in a window, for conflict checks on either provider."""
    window = provider.list_events(250, start, end)
    if not window.get("success"):
        return None
    intervals = []
    for event in window.get("events", []):
        if exclude_event_ids and event.get("event_id") in exclude_event_ids:
            continue
        if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
            continue
        interval = event_interval(event)
        if interval:
            intervals.append(interval)
    return intervals
