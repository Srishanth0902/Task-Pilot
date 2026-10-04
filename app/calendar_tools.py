"""LangChain tools backed by whichever calendar provider is active.

The tools call the provider interface rather than Google directly, so the same
five tools drive Google Calendar and the native calendar with no branching and
no duplicated agent logic.
"""

from datetime import datetime

from langchain_core.tools import StructuredTool

from app.schemas import (
    CreateEventInput,
    DeleteEventInput,
    GetEventsInput,
    SearchEventInput,
    UpdateEventInput,
)


def build_calendar_tools(provider):
    """Bind a calendar provider to five LangChain tools.

    Dependency injection keeps OAuth out of module import, lets the same tools
    run against a fake client in offline tests, and is what allows the native
    calendar to reuse the agent unchanged.

    A bare Google service object is still accepted so older callers and tests
    keep working; it is wrapped in the Google provider.
    """
    provider = as_provider(provider)

    def create_calendar_event(
        title: str,
        start_time: datetime,
        end_time: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict:
        """Create a calendar event. Omitted end times default to one hour."""
        values = CreateEventInput(
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
        )
        return provider.create_event(
            summary=values.title,
            start=values.start_time,
            end=values.end_time,
            description=values.description,
            location=values.location,
        )

    def list_calendar_events(
        max_results: int = 10,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
    ) -> dict:
        """List upcoming calendar events in chronological order."""
        return provider.list_events(
            max_results=max_results,
            time_min=time_min,
            time_max=time_max,
        )

    def search_calendar_events(
        query: str,
        max_results: int = 10,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
    ) -> dict:
        """Find calendar events whose text matches a query."""
        return provider.search_events(
            query=query,
            max_results=max_results,
            time_min=time_min,
            time_max=time_max,
        )

    def update_calendar_event(
        event_id: str,
        title: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict:
        """Change selected fields on an existing calendar event."""
        return provider.update_event(
            event_id=event_id,
            summary=title,
            start=start_time,
            end=end_time,
            description=description,
            location=location,
        )

    def delete_calendar_event(event_id: str) -> dict:
        """Delete a calendar event by its event id."""
        return provider.delete_event(event_id=event_id)

    return [
        StructuredTool.from_function(
            create_calendar_event,
            name="create_calendar_event",
            args_schema=CreateEventInput,
        ),
        StructuredTool.from_function(
            list_calendar_events,
            name="list_calendar_events",
            args_schema=GetEventsInput,
        ),
        StructuredTool.from_function(
            search_calendar_events,
            name="search_calendar_events",
            args_schema=SearchEventInput,
        ),
        StructuredTool.from_function(
            update_calendar_event,
            name="update_calendar_event",
            args_schema=UpdateEventInput,
        ),
        StructuredTool.from_function(
            delete_calendar_event,
            name="delete_calendar_event",
            args_schema=DeleteEventInput,
        ),
    ]


def as_provider(candidate):
    """Accept a provider, or wrap a raw Google service in one.

    Existing callers and a large body of tests pass the Google client directly.
    Rather than rewrite every one of them, anything that is not already a
    provider is treated as a Google service.
    """
    from app.calendar_provider import CalendarProvider, GoogleCalendarProvider

    if isinstance(candidate, CalendarProvider):
        return candidate
    return GoogleCalendarProvider(candidate)
