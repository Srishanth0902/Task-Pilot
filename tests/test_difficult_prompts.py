"""Adversarial conversation regressions, with no real calendar mutations.

Scripted plans deliberately include imperfect model output to test graph safety.
The separate live runner exercises actual natural-language interpretation.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.graph_agent import CalendarConversation, QueryPlan
from app.scheduling import build_bulk_changes, find_free_slots, overlapping_events
from app.date_utils import infer_local_time_window
from tests.test_advanced_agent import Planner, event
from tests.test_calendar_service import FakeService


class DifficultPromptTests(unittest.TestCase):
    def test_transparent_timed_event_can_still_be_rescheduled(self):
        changes = build_bulk_changes([{"event_id": "one", "title": "Study", "transparency": "transparent",
            "start": "2026-09-13T18:00:00+05:30", "end": "2026-09-13T19:00:00+05:30"}],
            {"intent": "bulk_update", "shift_minutes": 60})
        self.assertEqual(changes[0]["new_start"], "2026-09-13T19:00:00+05:30")
    def test_explicit_evening_bound_overrides_model_whole_day(self):
        chat = CalendarConversation(None, FakeService(), planner=Planner(QueryPlan(intent="free_slot",
            time_min="2026-09-13T00:00:00+05:30", time_max="2026-09-14T00:00:00+05:30")))
        with patch("app.graph_agent.local_now", return_value=datetime.fromisoformat("2026-09-12T09:00:00+05:30")):
            result = chat.ask("Show slots tomorrow after 6 PM")
        self.assertEqual(result["alternatives"][0]["start"], "2026-09-13T18:00:00+05:30")

    def test_partial_bulk_failure_is_not_reported_as_success(self):
        from googleapiclient.errors import HttpError
        from tests.test_calendar_service import FakeResponse
        service = FakeService(delete_error=HttpError(FakeResponse(403), b"forbidden"), list_result={"items": [
            event("a", "Study", "2026-09-13T18:00:00+05:30", "2026-09-13T19:00:00+05:30")]})
        chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="bulk_delete")))
        chat.ask("Delete all tasks")
        result = chat.ask("yes")
        self.assertTrue(result["error"])
        self.assertFalse(result["verified"])
        self.assertNotIn("Successfully deleted", result["response"])

    def test_failed_planner_can_recover_on_next_turn(self):
        planner = Planner(QueryPlan(intent="create", title="Bad", start_time="invalid"), QueryPlan(intent="list"))
        chat = CalendarConversation(None, FakeService(), planner=planner)
        self.assertTrue(chat.ask("Add at an invalid time")["error"])
        result = chat.ask("Show my events")
        self.assertIsNone(result["error"])
        self.assertTrue(result["verified"])

    def test_relative_date_boundaries(self):
        for now, expected in [("2026-12-31T23:55:00+05:30", "2027-01-01"),
                              ("2028-02-28T22:00:00+05:30", "2028-02-29"),
                              ("2026-02-28T22:00:00+05:30", "2026-03-01")]:
            with self.subTest(reference=now):
                start, end = infer_local_time_window("tomorrow after 6 PM", now=datetime.fromisoformat(now))
                self.assertEqual(start.isoformat(), expected + "T18:00:00+05:30")
                self.assertEqual(end.isoformat(), expected + "T21:00:00+05:30")

    def test_multi_day_availability_checks_second_day(self):
        service = FakeService(list_result={"items": [{"id": "busy", "summary": "Leave",
            "start": {"date": "2026-09-13"}, "end": {"date": "2026-09-14"}}]})
        chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="free_slot",
            time_min="2026-09-13T08:00:00+05:30", time_max="2026-09-15T00:00:00+05:30")))
        result = chat.ask("Show available slots on September 13 and 14")
        self.assertTrue(result["verified"])
        self.assertTrue(result["alternatives"][0]["start"].startswith("2026-09-14"))

    def test_today_availability_never_returns_elapsed_time(self):
        chat = CalendarConversation(None, FakeService(), planner=Planner(QueryPlan(intent="free_slot",
            time_min="2026-09-12T08:00:00+05:30", time_max="2026-09-12T21:00:00+05:30")))
        with patch("app.graph_agent.local_now", return_value=datetime.fromisoformat("2026-09-12T18:00:00+05:30")):
            result = chat.ask("Find slots today")
        self.assertEqual(result["alternatives"][0]["start"], "2026-09-12T18:00:00+05:30")

    def test_model_search_plan_cannot_drop_explicit_rename(self):
        service = FakeService(list_result={"items": [event("yoga", "Yoga", "2026-09-13T07:00:00+05:30", "2026-09-13T08:00:00+05:30")]})
        chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="search", search_query="Yoga")))
        result = chat.ask("Rename Yoga to Morning exercise")
        self.assertEqual(result["intent"], "update")
        self.assertEqual(result["tool_result"]["title"], "Morning exercise")

    def test_generic_session_suffix_does_not_hide_matching_study_events(self):
        service = FakeService()
        chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="search", search_query="study sessions")))
        chat.ask("Find study sessions tomorrow")
        self.assertEqual(service.events().calls[0][1]["q"], "study")

    def test_natural_confirmation_phrases(self):
        for phrase, deletes in [("Yes, proceed", 1), ("No, cancel it", 0),
                                ("Yes please", 1), ("No thanks", 0),
                                ("yes, but only the first one", 0)]:
            with self.subTest(prompt=phrase):
                service = FakeService()
                chat = CalendarConversation(None, service, planner=Planner(
                    QueryPlan(intent="delete", event_id="one")))
                chat.ask("Delete event id one")
                result = chat.ask(phrase)
                self.assertEqual(sum(n == "delete" for n, _ in service.events().calls), deletes)
                if phrase == "No, cancel it":
                    self.assertFalse(result["awaiting_confirmation"])

    def test_ordinal_followup_selects_exact_event(self):
        for phrase in ["The second one", "2", "option 2"]:
            with self.subTest(prompt=phrase):
                service = FakeService(list_result={"items": [
                    event("a", "Meeting", "2026-09-13T10:00:00+05:30", "2026-09-13T11:00:00+05:30"),
                    event("b", "Meeting", "2026-09-13T15:00:00+05:30", "2026-09-13T16:00:00+05:30")]})
                chat = CalendarConversation(None, service, planner=Planner(
                    QueryPlan(intent="delete", search_query="Meeting"),
                    QueryPlan(intent="unknown", continue_previous=True)))
                chat.ask("Delete my meeting")
                result = chat.ask(phrase)
                self.assertTrue(result["awaiting_confirmation"])
                self.assertEqual(result["pending_action"]["event_id"], "b")
                self.assertFalse(any(n == "delete" for n, _ in service.events().calls))

    def test_invalid_ordinal_never_selects_event(self):
        service = FakeService(list_result={"items": [
            event("a", "Meeting", "2026-09-13T10:00:00+05:30", "2026-09-13T11:00:00+05:30"),
            event("b", "Meeting", "2026-09-13T15:00:00+05:30", "2026-09-13T16:00:00+05:30")]})
        chat = CalendarConversation(None, service, planner=Planner(
            QueryPlan(intent="delete", search_query="Meeting"),
            QueryPlan(intent="unknown", continue_previous=True)))
        chat.ask("Delete my meeting")
        result = chat.ask("The ninth one")
        self.assertFalse(result["awaiting_confirmation"])

    def test_time_selection_uses_destination_not_source(self):
        service = FakeService(list_result={"items": [event(
            "a", "DSA", "2026-09-13T18:00:00+05:30", "2026-09-13T19:00:00+05:30")]})
        chat = CalendarConversation(None, service, planner=Planner(QueryPlan(
            intent="update", search_query="DSA", start_time="2026-09-13T20:00:00+05:30",
            end_time="2026-09-13T21:00:00+05:30")))
        result = chat.ask("Move DSA at 6 PM to 8 PM")
        self.assertTrue(result["verified"])
        self.assertEqual(result["tool_result"]["start"], "2026-09-13T20:00:00+05:30")

    def test_explicit_utc_is_converted_without_overwriting_clock(self):
        service = FakeService()
        chat = CalendarConversation(None, service, planner=Planner(QueryPlan(
            intent="create", title="Call", start_time="2026-09-13T18:00:00Z",
            end_time="2026-09-13T19:00:00Z")))
        result = chat.ask("Add Call on September 13 at 6 PM UTC for an hour")
        self.assertEqual(result["tool_result"]["start"], "2026-09-13T23:30:00+05:30")

    def test_all_day_busy_blocks_scheduling_but_transparent_does_not(self):
        start = datetime.fromisoformat("2026-09-13T18:00:00+05:30")
        for transparent in [False, True]:
            with self.subTest(transparent=transparent):
                busy = {"event_id": "leave", "start": "2026-09-13", "end": "2026-09-14",
                        "transparency": "transparent" if transparent else "opaque"}
                self.assertEqual(bool(overlapping_events([busy], start, start + timedelta(hours=1))), not transparent)
                self.assertEqual(bool(find_free_slots([busy], start, start + timedelta(hours=1), timedelta(hours=1))), transparent)

    def test_cancelled_event_does_not_block_slot(self):
        start = datetime.fromisoformat("2026-09-13T18:00:00+05:30")
        busy = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(), "status": "cancelled"}
        self.assertFalse(overlapping_events([busy], start, start + timedelta(hours=1)))

    def test_availability_empty_occupied_and_partial_windows(self):
        for busy, count in [([], 3), ([event("a", "Busy", "2026-09-13T18:00:00+05:30", "2026-09-13T21:00:00+05:30")], 0),
                            ([event("a", "Busy", "2026-09-13T18:00:00+05:30", "2026-09-13T19:00:00+05:30")], 2)]:
            with self.subTest(expected_slots=count):
                service = FakeService(list_result={"items": busy})
                chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="free_slot")))
                with patch("app.graph_agent.local_now", return_value=datetime.fromisoformat("2026-09-12T10:00:00+05:30")):
                    result = chat.ask("Show slots tomorrow after 6 PM")
                self.assertEqual(result["tool_result"]["count"], count)
                self.assertTrue(all(n == "list" for n, _ in service.events().calls))

    def test_invalid_model_time_returns_error_without_writes(self):
        for timestamp in ["not-a-date", "2026-02-30T18:00:00", "2026-09-13T25:00:00"]:
            with self.subTest(timestamp=timestamp):
                service = FakeService()
                chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="create", title="DSA", start_time=timestamp)))
                result = chat.ask("Add DSA at an invalid time")
                self.assertTrue(result["error"])
                self.assertFalse(service.events().calls)

    def test_bulk_never_mutates_before_confirmation(self):
        for intent, phrase in [("bulk_delete", "Delete everything"), ("bulk_update", "Move all study sessions by 1 hour")]:
            with self.subTest(prompt=phrase):
                service = FakeService(list_result={"items": [event("a", "Study", "2026-09-13T18:00:00+05:30", "2026-09-13T19:00:00+05:30")]})
                chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent=intent, shift_minutes=60)))
                result = chat.ask(phrase)
                self.assertTrue(result["awaiting_confirmation"])
                self.assertTrue(all(n == "list" for n, _ in service.events().calls))

    def test_unknown_and_missing_fields_do_not_write(self):
        for plan, phrase in [(QueryPlan(intent="unknown"), "Do something useful"),
                             (QueryPlan(intent="create"), "Add an event"),
                             (QueryPlan(intent="update"), "Move it later")]:
            with self.subTest(prompt=phrase):
                service = FakeService()
                result = CalendarConversation(None, service, planner=Planner(plan)).ask(phrase)
                self.assertTrue(result["response"])
                self.assertFalse(any(n in {"insert", "patch", "delete"} for n, _ in service.events().calls))
