"""LangChain tools backed by the structured Google Calendar service."""

from datetime import datetime

from langchain_core.tools import StructuredTool

from app.calendar_service import (
    create_event,
    delete_event,
    get_events,
    search_events,
    update_event,
)
from app.schemas import (
    CreateEventInput,
    DeleteEventInput,
    GetEventsInput,
    SearchEventInput,
    UpdateEventInput,
)


def build_calendar_tools(service):
    """Bind an authenticated (or fake) service to five LangChain tools.

    Dependency injection keeps OAuth out of module import and makes the exact
    same tools usable with a fake Calendar client in offline tests.
    """

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
        return create_event(
            service,
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
        return get_events(
            service,
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
        return search_events(
            service,
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
        return update_event(
            service,
            event_id=event_id,
            summary=title,
            start=start_time,
            end=end_time,
            description=description,
            location=location,
        )

    def delete_calendar_event(event_id: str) -> dict:
        """Delete a calendar event by its event id."""
        return delete_event(service, event_id=event_id)

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
