"""The agent driving the native calendar: the same graph, a different backend.

These exercise the requests named in the brief end to end — create, move,
delete, bulk change, free-slot discovery — against the native provider, with
no Google client anywhere in the picture.
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from app.calendar_provider import NATIVE, NativeCalendarProvider
from app.graph_agent import CalendarConversation, QueryPlan
from app.user_store import UserStore
from tests.test_advanced_agent import Planner
from tests.test_multiuser import Model

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=IST)


class NativeAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        self.provider = NativeCalendarProvider(self.store, "alice")

    def _chat(self, *plans, preferences=None):
        return CalendarConversation(
            Model(Planner(*plans)), self.provider, preferences=preferences or {}
        )

    def _seed(self, title, hour, minutes=60, day=5):
        start = datetime(2026, 10, day, hour, 0, tzinfo=IST)
        return self.provider.create_event(
            title, start, start + timedelta(minutes=minutes)
        )

    # ---- "Add yoga tomorrow at 7 AM for one hour." -----------------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_create_writes_to_the_database(self, _now):
        chat = self._chat(QueryPlan(
            intent="create", title="Yoga",
            start_time="2026-10-05T07:00:00+05:30",
            end_time="2026-10-05T08:00:00+05:30",
        ))
        chat.ask("Add yoga tomorrow at 7 AM for one hour.", thread_id="t1")

        stored = self.provider.search_events("yoga")
        self.assertEqual(stored["count"], 1)
        self.assertEqual(stored["events"][0]["start"][11:16], "01:30")  # 07:00 IST in UTC

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_created_event_is_reported_in_ist(self, _now):
        chat = self._chat(QueryPlan(
            intent="create", title="Yoga",
            start_time="2026-10-05T07:00:00+05:30",
            end_time="2026-10-05T08:00:00+05:30",
            duration_minutes=60,
        ))
        state = chat.ask("Add yoga tomorrow at 7 AM for an hour.", thread_id="t1")
        self.assertIn("7:00", state["response"])

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_create_needs_no_google_credentials(self, _now):
        """No Google client exists in this test at all."""
        chat = self._chat(QueryPlan(
            intent="create", title="Yoga",
            start_time="2026-10-05T07:00:00+05:30",
            end_time="2026-10-05T08:00:00+05:30",
        ))
        state = chat.ask("Add yoga tomorrow at 7 AM.", thread_id="t1")
        self.assertIsNone(state.get("error"))

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_failure_is_not_reported_as_success(self, _now):
        """Claiming success for a write that did not happen is the worst bug."""
        chat = self._chat(QueryPlan(
            intent="create", title="Backwards",
            start_time="2026-10-05T09:00:00+05:30",
            end_time="2026-10-05T08:00:00+05:30",
        ))
        state = chat.ask("Add something impossible", thread_id="t1")
        result = state.get("tool_result") or {}
        self.assertFalse(result.get("success", False))
        self.assertEqual(self.provider.list_events(10)["count"], 0)

    # ---- "Move my study session to 8 PM." --------------------------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_update_moves_a_stored_event(self, _now):
        created = self._seed("Study session", 18)
        chat = self._chat(
            QueryPlan(intent="update", search_query="study session",
                      start_time="2026-10-05T20:00:00+05:30",
                      end_time="2026-10-05T21:00:00+05:30"),
        )
        chat.ask("Move my study session to 8 PM.", thread_id="t1")

        moved = self.provider.get_event(created["event_id"])
        self.assertEqual(moved["start"][11:16], "14:30")  # 20:00 IST in UTC

    # ---- "Delete my meeting." --------------------------------------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_delete_asks_before_removing_anything(self, _now):
        """The destructive-action safeguard must hold on the native calendar."""
        created = self._seed("Project meeting", 15)
        chat = self._chat(QueryPlan(intent="delete", search_query="meeting"))
        state = chat.ask("Delete my meeting.", thread_id="t1")

        self.assertTrue(state.get("awaiting_confirmation"))
        self.assertIsNotNone(self.provider.get_event(created["event_id"]))

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_delete_removes_the_event_once_confirmed(self, _now):
        created = self._seed("Project meeting", 15)
        chat = self._chat(QueryPlan(intent="delete", search_query="meeting"))
        chat.ask("Delete my meeting.", thread_id="t1")
        chat.ask("yes", thread_id="t1")
        self.assertIsNone(self.provider.get_event(created["event_id"]))

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_declining_a_delete_keeps_the_event(self, _now):
        created = self._seed("Project meeting", 15)
        chat = self._chat(QueryPlan(intent="delete", search_query="meeting"))
        chat.ask("Delete my meeting.", thread_id="t1")
        chat.ask("no", thread_id="t1")
        self.assertIsNotNone(self.provider.get_event(created["event_id"]))

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_ambiguous_delete_changes_nothing_without_a_choice(self, _now):
        """Two matches must produce a question, never a guess."""
        first = self._seed("Team meeting", 10)
        second = self._seed("Client meeting", 16)
        chat = self._chat(QueryPlan(intent="delete", search_query="meeting"))
        state = chat.ask("Delete my meeting.", thread_id="t1")

        self.assertIsNotNone(self.provider.get_event(first["event_id"]))
        self.assertIsNotNone(self.provider.get_event(second["event_id"]))
        self.assertTrue(state.get("response"))

    # ---- "Move all study sessions by one hour." --------------------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_bulk_change_waits_for_confirmation(self, _now):
        first = self._seed("Study session A", 9)
        second = self._seed("Study session B", 14)
        chat = self._chat(QueryPlan(intent="bulk_update", search_query="study session",
                                    shift_minutes=60))
        state = chat.ask("Move all study sessions by one hour.", thread_id="t1")

        self.assertTrue(state.get("awaiting_confirmation"))
        # Nothing moves until the user agrees: 09:00 and 14:00 IST unchanged.
        self.assertEqual(self.provider.get_event(first["event_id"])["start"][11:16], "03:30")
        self.assertEqual(self.provider.get_event(second["event_id"])["start"][11:16], "08:30")

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_bulk_change_applies_once_confirmed(self, _now):
        first = self._seed("Study session A", 9)
        chat = self._chat(QueryPlan(intent="bulk_update", search_query="study session",
                                    shift_minutes=60))
        chat.ask("Move all study sessions by one hour.", thread_id="t1")
        chat.ask("yes", thread_id="t1")
        # 09:00 IST shifted by an hour is 10:00 IST, which is 04:30 UTC.
        self.assertEqual(self.provider.get_event(first["event_id"])["start"][11:16], "04:30")

    # ---- "Find a two-hour free slot tomorrow after 6 PM." ----------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_free_slot_search_reads_the_native_calendar(self, _now):
        self._seed("Dinner", 18, minutes=60)
        chat = self._chat(QueryPlan(intent="free_slot", duration_minutes=120,
                                    time_min="2026-10-05T18:00:00+05:30",
                                    time_max="2026-10-05T23:00:00+05:30"))
        state = chat.ask("Find a two-hour free slot tomorrow after 6 PM.",
                         thread_id="t1")
        self.assertTrue(state.get("response"))
        self.assertIsNone(state.get("error"))

    # ---- follow-up context ------------------------------------------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_a_follow_up_keeps_the_thread(self, _now):
        self._seed("Study session", 18)
        chat = self._chat(
            QueryPlan(intent="list"),
            QueryPlan(intent="update", search_query="study session",
                      start_time="2026-10-05T20:00:00+05:30",
                      end_time="2026-10-05T21:00:00+05:30"),
        )
        chat.ask("What's on tomorrow?", thread_id="t1")
        state = chat.ask("Move the study session to 8 PM.", thread_id="t1")
        self.assertIsNone(state.get("error"))
        self.assertGreaterEqual(len(state.get("messages", [])), 2)

    # ---- isolation --------------------------------------------------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_the_agent_cannot_reach_another_workspace(self, _now):
        self._seed("Alice private", 11)
        other = CalendarConversation(
            Model(Planner(QueryPlan(intent="list"))),
            NativeCalendarProvider(self.store, "bob"),
            preferences={},
        )
        state = other.ask("What's on my calendar?", thread_id="t1")
        self.assertNotIn("Alice private", state.get("response", ""))

    # ---- provider identity on results -------------------------------------

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_tool_results_name_the_calendar_they_used(self, _now):
        self._seed("Study session", 18)
        chat = self._chat(QueryPlan(intent="list"))
        state = chat.ask("What's on tomorrow?", thread_id="t1")
        self.assertEqual((state.get("tool_result") or {}).get("provider"), NATIVE)


if __name__ == "__main__":
    unittest.main()
