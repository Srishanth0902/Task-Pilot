"""The native calendar provider, and the contract it shares with Google."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from app.calendar_provider import (
    GOOGLE,
    NATIVE,
    GoogleCalendarProvider,
    NativeCalendarProvider,
    busy_intervals,
)
from app.date_utils import local_now
from app.user_store import UserStore
from tests.test_calendar_service import FakeService


def _store():
    temp = tempfile.TemporaryDirectory()
    key = Fernet.generate_key()
    return UserStore(temp.name, key), temp, key


class NativeProviderTests(unittest.TestCase):
    def setUp(self):
        self.store, self.temp, self.key = _store()
        self.addCleanup(self.temp.cleanup)
        self.provider = NativeCalendarProvider(self.store, "alice")
        self.now = local_now().replace(minute=0, second=0, microsecond=0)

    def _make(self, title="Yoga", hours=24, minutes=60, **extra):
        start = self.now + timedelta(hours=hours)
        return self.provider.create_event(
            title, start, start + timedelta(minutes=minutes), **extra
        )

    # ---- create ----------------------------------------------------------

    def test_create_returns_the_shared_contract(self):
        created = self._make()
        self.assertTrue(created["success"])
        self.assertEqual(created["provider"], NATIVE)
        self.assertEqual(created["title"], "Yoga")
        for field in ("event_id", "etag", "start", "end", "status"):
            self.assertIn(field, created)

    def test_native_events_never_claim_a_google_link(self):
        """Inventing a Google URL for a native event would 404 the user."""
        self.assertIsNone(self._make()["html_link"])

    def test_create_rejects_an_empty_title(self):
        start = self.now + timedelta(hours=2)
        result = self.provider.create_event("   ", start, start + timedelta(hours=1))
        self.assertFalse(result["success"])

    def test_create_rejects_an_end_before_the_start(self):
        start = self.now + timedelta(hours=2)
        result = self.provider.create_event("Backwards", start, start - timedelta(hours=1))
        self.assertFalse(result["success"])

    def test_create_requires_no_google_credentials(self):
        """The whole point: native mode must work with no Google at all."""
        self.assertTrue(self._make()["success"])

    # ---- read ------------------------------------------------------------

    def test_list_returns_upcoming_events_in_order(self):
        self._make("Later", hours=48)
        self._make("Sooner", hours=24)
        listed = self.provider.list_events(10)
        self.assertTrue(listed["success"])
        self.assertEqual([e["title"] for e in listed["events"]], ["Sooner", "Later"])

    def test_list_excludes_the_past_by_default(self):
        self._make("Gone", hours=-48)
        self._make("Coming", hours=24)
        titles = [e["title"] for e in self.provider.list_events(10)["events"]]
        self.assertEqual(titles, ["Coming"])

    def test_list_honours_an_explicit_window(self):
        self._make("Inside", hours=24)
        self._make("Outside", hours=240)
        window = self.provider.list_events(
            10, self.now, self.now + timedelta(hours=48)
        )
        self.assertEqual([e["title"] for e in window["events"]], ["Inside"])

    def test_list_respects_max_results(self):
        for index in range(5):
            self._make(f"Event {index}", hours=24 + index)
        self.assertEqual(self.provider.list_events(2)["count"], 2)

    def test_search_matches_title_description_and_location(self):
        self._make("Study session", hours=24, description="Graph theory")
        self._make("Gym", hours=26, location="Fitness centre")
        self.assertEqual(self.provider.search_events("study")["count"], 1)
        self.assertEqual(self.provider.search_events("graph")["count"], 1)
        self.assertEqual(self.provider.search_events("fitness")["count"], 1)

    def test_search_is_case_insensitive(self):
        self._make("Yoga Class")
        self.assertEqual(self.provider.search_events("YOGA")["count"], 1)

    def test_search_without_a_match_is_an_empty_success(self):
        self._make("Yoga")
        result = self.provider.search_events("quidditch")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)

    def test_get_event(self):
        created = self._make()
        fetched = self.provider.get_event(created["event_id"])
        self.assertEqual(fetched["title"], "Yoga")

    def test_get_missing_event_is_none(self):
        self.assertIsNone(self.provider.get_event("nat_missing"))

    # ---- update ----------------------------------------------------------

    def test_update_changes_only_what_was_given(self):
        created = self._make(description="Morning practice")
        moved = self.provider.update_event(
            created["event_id"], start=self.now + timedelta(hours=30),
            end=self.now + timedelta(hours=31),
        )
        self.assertTrue(moved["success"])
        self.assertEqual(moved["title"], "Yoga")
        self.assertEqual(moved["description"], "Morning practice")
        self.assertNotEqual(moved["start"], created["start"])

    def test_update_bumps_the_version_token(self):
        created = self._make()
        moved = self.provider.update_event(created["event_id"], summary="Yoga II")
        self.assertNotEqual(moved["etag"], created["etag"])

    def test_update_with_no_fields_fails(self):
        created = self._make()
        self.assertFalse(self.provider.update_event(created["event_id"])["success"])

    def test_update_of_a_missing_event_fails(self):
        result = self.provider.update_event("nat_missing", summary="Ghost")
        self.assertFalse(result["success"])
        self.assertEqual(result.get("status_code"), 404)

    def test_update_cannot_invert_the_time_range(self):
        created = self._make()
        result = self.provider.update_event(
            created["event_id"], end=self.now + timedelta(hours=1)
        )
        self.assertFalse(result["success"])

    def test_conditional_update_rejects_a_stale_version(self):
        """The native equivalent of an etag mismatch must refuse the write."""
        created = self._make()
        self.provider.update_event(created["event_id"], summary="Changed elsewhere")
        result = self.provider.update_event(
            created["event_id"], summary="Mine", if_match=created["etag"]
        )
        self.assertFalse(result["success"])
        self.assertEqual(self.provider.get_event(created["event_id"])["title"],
                         "Changed elsewhere")

    def test_conditional_update_accepts_the_current_version(self):
        created = self._make()
        result = self.provider.update_event(
            created["event_id"], summary="Mine", if_match=created["etag"]
        )
        self.assertTrue(result["success"])

    # ---- delete ----------------------------------------------------------

    def test_delete(self):
        created = self._make()
        removed = self.provider.delete_event(created["event_id"])
        self.assertTrue(removed["success"])
        self.assertTrue(removed["deleted"])
        self.assertIsNone(self.provider.get_event(created["event_id"]))

    def test_deleting_twice_is_not_an_error(self):
        created = self._make()
        self.provider.delete_event(created["event_id"])
        again = self.provider.delete_event(created["event_id"])
        self.assertTrue(again["success"])
        self.assertFalse(again["deleted"])

    def test_conditional_delete_rejects_a_stale_version(self):
        created = self._make()
        self.provider.update_event(created["event_id"], summary="Moved on")
        result = self.provider.delete_event(created["event_id"], if_match=created["etag"])
        self.assertFalse(result["success"])
        self.assertIsNotNone(self.provider.get_event(created["event_id"]))

    # ---- isolation -------------------------------------------------------

    def test_one_workspace_cannot_see_another(self):
        mine = self._make("Private")
        other = NativeCalendarProvider(self.store, "bob")
        self.assertEqual(other.list_events(10)["count"], 0)
        self.assertIsNone(other.get_event(mine["event_id"]))

    def test_one_workspace_cannot_change_another(self):
        mine = self._make("Private")
        other = NativeCalendarProvider(self.store, "bob")
        self.assertFalse(other.update_event(mine["event_id"], summary="Hijacked")["success"])
        self.assertFalse(other.delete_event(mine["event_id"])["deleted"])
        self.assertEqual(self.provider.get_event(mine["event_id"])["title"], "Private")

    def test_event_text_is_not_stored_in_plaintext(self):
        self._make("Confidential therapy appointment")
        raw = (self.store.directory / "users.sqlite3").read_bytes()
        self.assertNotIn(b"Confidential therapy appointment", raw)

    # ---- persistence -----------------------------------------------------

    def test_events_survive_a_store_restart(self):
        """A native calendar that forgets on restart is not a calendar."""
        created = self._make("Durable")
        # Same directory, same key, a brand new store object: this is what a
        # process restart looks like to the database.
        reopened = UserStore(self.store.directory, self.key)
        provider = NativeCalendarProvider(reopened, "alice")
        found = provider.get_event(created["event_id"])
        self.assertIsNotNone(found)
        self.assertEqual(found["title"], "Durable")
        self.assertEqual(provider.list_events(10)["count"], 1)


class ProviderContractTests(unittest.TestCase):
    """Both providers must answer the same questions the same way."""

    def setUp(self):
        self.store, self.temp, self.key = _store()
        self.addCleanup(self.temp.cleanup)
        self.native = NativeCalendarProvider(self.store, "alice")
        self.google = GoogleCalendarProvider(FakeService(list_result={"items": []}))

    def test_both_report_their_identity(self):
        self.assertEqual(self.native.name, NATIVE)
        self.assertEqual(self.google.name, GOOGLE)

    def test_both_have_a_human_label(self):
        self.assertEqual(self.native.label, "Task Pilot calendar")
        self.assertEqual(self.google.label, "Google Calendar")

    def test_both_stamp_results_with_their_provider(self):
        self.assertEqual(self.native.list_events(5)["provider"], NATIVE)
        self.assertEqual(self.google.list_events(5)["provider"], GOOGLE)

    def test_both_expose_the_same_methods(self):
        for name in ("list_events", "search_events", "create_event",
                     "update_event", "delete_event", "get_event", "close"):
            self.assertTrue(callable(getattr(self.native, name)), name)
            self.assertTrue(callable(getattr(self.google, name)), name)

    def test_closing_a_native_provider_is_harmless(self):
        self.native.close()


class BusyIntervalTests(unittest.TestCase):
    def setUp(self):
        self.store, self.temp, self.key = _store()
        self.addCleanup(self.temp.cleanup)
        self.provider = NativeCalendarProvider(self.store, "alice")
        self.now = local_now().replace(minute=0, second=0, microsecond=0)

    def test_reports_occupied_intervals(self):
        start = self.now + timedelta(hours=24)
        self.provider.create_event("Busy", start, start + timedelta(hours=1))
        found = busy_intervals(self.provider, self.now, self.now + timedelta(days=2))
        self.assertEqual(len(found), 1)

    def test_excludes_a_named_event(self):
        start = self.now + timedelta(hours=24)
        created = self.provider.create_event("Busy", start, start + timedelta(hours=1))
        found = busy_intervals(
            self.provider, self.now, self.now + timedelta(days=2),
            exclude_event_ids={created["event_id"]},
        )
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
