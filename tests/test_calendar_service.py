"""Tests for the calendar helpers, driven against a fake Google client.

These use unittest and a fake Google client. They verify the request bodies we
send to Google, which is the part that is easy to get wrong and impossible to
check by reading the code alone.

Run them with:

    python -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from app import calendar_service
from app.calendar_service import (
    create_event,
    delete_event,
    get_events,
    search_events,
    update_event,
)
from app.main import _start_of


class FakeResponse:
    """Minimal stand-in for an httplib2 response, enough for HttpError."""

    def __init__(self, status):
        self.status = status
        self.reason = "fake"


class FakeRequest:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class FakeEvents:
    """Records the calls made to it so tests can assert on request bodies."""

    def __init__(
        self,
        list_result=None,
        list_error=None,
        insert_error=None,
        patch_error=None,
        delete_error=None,
    ):
        self.calls = []
        self._list_result = list_result if list_result is not None else {"items": []}
        self._list_error = list_error
        self._insert_error = insert_error
        self._patch_error = patch_error
        self._delete_error = delete_error

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return FakeRequest(self._list_result, error=self._list_error)

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return FakeRequest(
            {"id": "new-id", "htmlLink": "http://example/new", **kwargs["body"]},
            error=self._insert_error,
        )

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        return FakeRequest(
            {"id": kwargs["eventId"], **kwargs["body"]},
            error=self._patch_error,
        )

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return FakeRequest(result="", error=self._delete_error)


class FakeService:
    def __init__(self, **kwargs):
        self._events = FakeEvents(**kwargs)

    def events(self):
        return self._events


class GetEventsTests(unittest.TestCase):
    def test_requests_an_ordered_expanded_agenda(self):
        service = FakeService(list_result={"items": [{"summary": "A"}]})

        result = get_events(service, max_results=7)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["title"], "A")
        name, kwargs = service.events().calls[0]
        self.assertEqual(name, "list")
        self.assertEqual(kwargs["maxResults"], 7)
        self.assertEqual(kwargs["calendarId"], calendar_service.CALENDAR_ID)
        # Recurring events must be expanded, and the agenda must be in order.
        self.assertTrue(kwargs["singleEvents"])
        self.assertEqual(kwargs["orderBy"], "startTime")
        self.assertIn("timeMin", kwargs)

    def test_empty_calendar_returns_empty_list_not_none(self):
        result = get_events(FakeService(list_result={}))
        self.assertEqual(result["events"], [])
        self.assertEqual(result["count"], 0)

    def test_normalises_google_utc_times_to_the_configured_timezone(self):
        service = FakeService(
            list_result={
                "items": [
                    {
                        "summary": "ML study",
                        "start": {"dateTime": "2026-09-11T12:30:00Z"},
                        "end": {"dateTime": "2026-09-11T13:30:00Z"},
                    }
                ]
            }
        )

        event = get_events(service)["events"][0]

        self.assertEqual(event["start"], "2026-09-11T18:00:00+05:30")
        self.assertEqual(event["end"], "2026-09-11T19:00:00+05:30")

    def test_google_list_failure_is_structured(self):
        denied = HttpError(FakeResponse(403), b"forbidden")
        result = get_events(FakeService(list_error=denied))

        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 403)


class SearchEventsTests(unittest.TestCase):
    def test_searches_with_text_and_structured_results(self):
        service = FakeService(
            list_result={"items": [{"id": "evt-1", "summary": "DSA Study"}]}
        )

        result = search_events(service, "DSA", max_results=5)

        self.assertTrue(result["success"])
        self.assertEqual(result["query"], "DSA")
        self.assertEqual(result["events"][0]["event_id"], "evt-1")
        _, kwargs = service.events().calls[0]
        self.assertEqual(kwargs["q"], "DSA")
        self.assertEqual(kwargs["maxResults"], 5)


class CreateEventTests(unittest.TestCase):
    def test_sends_rfc3339_times_with_the_configured_timezone(self):
        service = FakeService()

        create_event(
            service,
            summary="Agentic AI Project Work",
            start=datetime(2026, 8, 24, 18, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
            end=datetime(2026, 8, 24, 19, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        )

        _, kwargs = service.events().calls[0]
        body = kwargs["body"]
        self.assertEqual(body["summary"], "Agentic AI Project Work")
        self.assertEqual(body["start"]["dateTime"], "2026-08-24T18:00:00+05:30")
        self.assertEqual(body["end"]["dateTime"], "2026-08-24T19:00:00+05:30")
        self.assertEqual(body["start"]["timeZone"], calendar_service.TIMEZONE)

    def test_optional_fields_are_omitted_when_not_given(self):
        service = FakeService()

        create_event(
            service,
            "x",
            datetime(2026, 8, 24, 1, tzinfo=ZoneInfo("Asia/Kolkata")),
            datetime(2026, 8, 24, 2, tzinfo=ZoneInfo("Asia/Kolkata")),
        )

        body = service.events().calls[0][1]["body"]
        self.assertNotIn("description", body)
        self.assertNotIn("location", body)

    def test_returns_the_week_2_structured_contract(self):
        zone = ZoneInfo("Asia/Kolkata")
        result = create_event(
            FakeService(),
            "DSA Study",
            datetime(2026, 8, 25, 18, tzinfo=zone),
            datetime(2026, 8, 25, 19, tzinfo=zone),
        )

        self.assertEqual(result["success"], True)
        self.assertEqual(result["event_id"], "new-id")
        self.assertEqual(result["title"], "DSA Study")
        self.assertEqual(result["start"], "2026-08-25T18:00:00+05:30")

    def test_rejects_naive_datetimes(self):
        with self.assertRaises(ValueError):
            create_event(
                FakeService(),
                "ambiguous",
                datetime(2026, 8, 24, 1),
                datetime(2026, 8, 24, 2),
            )

    def test_google_create_failure_is_structured(self):
        denied = HttpError(FakeResponse(403), b"forbidden")
        zone = ZoneInfo("Asia/Kolkata")
        result = create_event(
            FakeService(insert_error=denied),
            "DSA",
            datetime(2026, 9, 11, 18, tzinfo=zone),
            datetime(2026, 9, 11, 19, tzinfo=zone),
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 403)


class UpdateEventTests(unittest.TestCase):
    def test_patches_only_the_fields_supplied(self):
        service = FakeService()

        update_event(service, "evt-1", summary="renamed")

        name, kwargs = service.events().calls[0]
        self.assertEqual(name, "patch")
        self.assertEqual(kwargs["eventId"], "evt-1")
        # Only the summary — a start time we did not pass must not be sent.
        self.assertEqual(kwargs["body"], {"summary": "renamed"})

    def test_moving_an_event_sends_both_times_with_timezone(self):
        service = FakeService()

        update_event(
            service,
            "evt-1",
            start=datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
            end=datetime(2026, 8, 24, 22, 30, tzinfo=ZoneInfo("Asia/Kolkata")),
        )

        body = service.events().calls[0][1]["body"]
        self.assertEqual(
            body["start"]["dateTime"], "2026-08-24T22:00:00+05:30"
        )
        self.assertEqual(body["end"]["timeZone"], calendar_service.TIMEZONE)
        self.assertNotIn("summary", body)

    def test_rejects_a_call_that_would_change_nothing(self):
        service = FakeService()

        with self.assertRaises(ValueError):
            update_event(service, "evt-1")

        self.assertEqual(service.events().calls, [])

    def test_google_update_failure_is_structured(self):
        denied = HttpError(FakeResponse(403), b"forbidden")
        result = update_event(
            FakeService(patch_error=denied), "evt-1", summary="New title"
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 403)


class DeleteEventTests(unittest.TestCase):
    def test_returns_true_when_the_event_is_removed(self):
        service = FakeService()

        result = delete_event(service, "evt-1")
        self.assertTrue(result["success"])
        self.assertTrue(result["deleted"])
        self.assertEqual(service.events().calls[0][1]["eventId"], "evt-1")

    def test_already_deleted_is_reported_rather_than_raised(self):
        # Google answers 410 Gone for an event that is already deleted.
        gone = HttpError(FakeResponse(410), b"gone")

        result = delete_event(FakeService(delete_error=gone), "evt-1")
        self.assertTrue(result["success"])
        self.assertFalse(result["deleted"])

    def test_a_real_failure_is_returned_as_structured_data(self):
        denied = HttpError(FakeResponse(403), b"forbidden")

        result = delete_event(FakeService(delete_error=denied), "evt-1")
        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 403)


class StartFormattingTests(unittest.TestCase):
    def test_timed_event_is_rendered_as_date_and_time(self):
        event = {"start": {"dateTime": "2026-08-24T10:00:00+05:30"}}

        self.assertEqual(_start_of(event), "2026-08-24 10:00")

    def test_all_day_event_is_labelled_instead_of_faking_midnight(self):
        self.assertEqual(
            _start_of({"start": {"date": "2026-08-25"}}), "2026-08-25 (all day)"
        )

    def test_missing_start_does_not_crash_the_agenda(self):
        self.assertEqual(_start_of({}), "unknown")


if __name__ == "__main__":
    unittest.main()
