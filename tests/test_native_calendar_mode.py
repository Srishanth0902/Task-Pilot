"""Guest workspaces, provider selection, and the no-silent-fallback rule."""

import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.calendar_provider import GOOGLE, NATIVE, NativeCalendarProvider
from app.date_utils import local_now
from app.graph_agent import QueryPlan
from app.multiuser import COOKIE, UserRuntime, create_multiuser_app
from app.user_store import UserStore
from tests.test_advanced_agent import Planner
from googleapiclient.errors import HttpError

from tests.test_calendar_service import FakeResponse, FakeService
from tests.test_multiuser import Model


class GuestWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())

    def test_guest_has_its_own_identity(self):
        first, second = self.store.create_guest(), self.store.create_guest()
        self.assertNotEqual(first, second)
        self.assertTrue(self.store.is_guest(first))

    def test_guest_ids_are_not_guessable(self):
        """A guest workspace is only as private as its identifier."""
        identity = self.store.create_guest()
        self.assertTrue(identity.startswith("guest_"))
        self.assertGreaterEqual(len(identity), 24)

    def test_guests_are_isolated_from_each_other(self):
        """Guests must be separate workspaces, never one shared account."""
        alice, bob = self.store.create_guest(), self.store.create_guest()
        NativeCalendarProvider(self.store, alice).create_event(
            "Alice private", local_now() + timedelta(hours=2),
            local_now() + timedelta(hours=3),
        )
        self.assertEqual(
            NativeCalendarProvider(self.store, bob).list_events(10)["count"], 0
        )

    def test_guest_starts_on_the_native_calendar(self):
        identity = self.store.create_guest()
        self.assertEqual(self.store.active_calendar(identity), NATIVE)

    def test_guest_has_no_google_credentials(self):
        identity = self.store.create_guest()
        self.assertFalse(self.store.google_connected(identity))

    def test_a_signed_in_account_is_not_a_guest(self):
        self.store.save_user("alice", {"id": "alice", "email": "a@example.com"},
                             {"refresh_token": "x"})
        self.assertFalse(self.store.is_guest("alice"))


class CalendarResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key = Fernet.generate_key()
        self.store = UserStore(self.temp.name, self.key)

    def _account(self, user_id, connected=True):
        self.store.save_user(user_id, {"id": user_id, "email": user_id + "@example.com"},
                             {"refresh_token": "x"} if connected else {})
        return user_id

    def test_google_is_the_default_when_connected(self):
        self.assertEqual(self.store.active_calendar(self._account("alice")), GOOGLE)

    def test_native_is_the_default_without_google(self):
        account = self._account("alice", connected=False)
        self.assertEqual(self.store.active_calendar(account), NATIVE)

    def test_an_explicit_choice_wins_over_the_default(self):
        account = self._account("alice")
        self.store.set_calendar_provider(account, NATIVE)
        self.assertEqual(self.store.active_calendar(account), NATIVE)

    def test_choice_survives_a_store_restart(self):
        account = self._account("alice")
        self.store.set_calendar_provider(account, NATIVE)
        reopened = UserStore(self.store.directory, self.key)
        self.assertEqual(reopened.active_calendar(account), NATIVE)

    def test_unknown_calendar_is_refused(self):
        account = self._account("alice")
        with self.assertRaises(ValueError):
            self.store.set_calendar_provider(account, "dropbox")

    def test_choice_does_not_leak_between_accounts(self):
        alice, bob = self._account("alice"), self._account("bob")
        self.store.set_calendar_provider(alice, NATIVE)
        self.assertEqual(self.store.active_calendar(bob), GOOGLE)


class CalendarApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        self.store.save_user("alice", {"id": "alice", "email": "alice@example.com"},
                             {"refresh_token": "x"})
        self.services = {"alice": FakeService(list_result={"items": []})}
        self.runtime = UserRuntime(
            self.store,
            lambda: Model(Planner(QueryPlan(intent="list"))),
            lambda uid: self.services[uid],
        )
        self.app = create_multiuser_app(self.store, self.runtime,
                                        origin="http://127.0.0.1:5173")

    def _client(self, user_id=None):
        client = TestClient(self.app)
        if user_id:
            client.cookies.set(COOKIE, self.store.session(user_id))
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        return client

    # ---- guest entry -----------------------------------------------------

    def test_guest_entry_issues_a_session_cookie(self):
        client = TestClient(self.app)
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        response = client.post("/auth/guest")
        self.assertEqual(response.status_code, 200)
        self.assertIn(COOKIE, response.cookies)
        self.assertEqual(response.json()["active_provider"], NATIVE)

    def test_guest_cookie_is_http_only(self):
        """A session the page's JavaScript can read is a session it can leak."""
        client = TestClient(self.app)
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        header = client.post("/auth/guest").headers["set-cookie"]
        self.assertIn("HttpOnly", header)

    def test_guest_entry_works_with_no_google_configured(self):
        client = TestClient(self.app)
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        self.assertTrue(client.post("/auth/guest").json()["success"])

    def test_guest_can_then_use_the_calendar(self):
        client = TestClient(self.app)
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        client.post("/auth/guest")
        self.assertEqual(client.get("/events").status_code, 200)

    def test_guest_entry_keeps_an_existing_signed_in_workspace(self):
        """Clicking guest while signed in must not strand the real workspace."""
        client = self._client("alice")
        before = client.get("/auth/me").json()["user"]["id"]
        client.post("/auth/guest")
        self.assertEqual(client.get("/auth/me").json()["user"]["id"], before)

    def test_two_guests_do_not_share_a_workspace(self):
        guests = []
        for _ in range(2):
            client = TestClient(self.app)
            client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
            client.post("/auth/guest")
            guests.append(client)
        start = local_now() + timedelta(hours=5)
        first_id = self.store.session_user(guests[0].cookies[COOKIE])
        self.store.create_native_event(first_id, title="Only mine", start=start,
                                       end=start + timedelta(hours=1))
        self.assertEqual(guests[0].get("/events").json()["count"], 1)
        self.assertEqual(guests[1].get("/events").json()["count"], 0)

    # ---- status and switching -------------------------------------------

    def test_status_requires_sign_in(self):
        self.assertEqual(self._client().get("/calendar/status").status_code, 401)

    def test_status_reports_the_active_calendar(self):
        body = self._client("alice").get("/calendar/status").json()
        self.assertEqual(body["active_provider"], GOOGLE)
        self.assertEqual(body["active_provider_label"], "Google Calendar")
        self.assertTrue(body["google_connected"])

    def test_status_lists_what_is_available(self):
        names = [p["name"] for p in self._client("alice").get("/calendar/status").json()
                 ["available_providers"]]
        self.assertIn(NATIVE, names)

    def test_switch_to_native_and_back(self):
        client = self._client("alice")
        self.assertEqual(
            client.put(f"/calendar/provider?provider={NATIVE}").json()["active_provider"],
            NATIVE,
        )
        self.assertEqual(
            client.put(f"/calendar/provider?provider={GOOGLE}").json()["active_provider"],
            GOOGLE,
        )

    def test_switching_to_google_without_an_account_is_refused(self):
        """Pointing someone at a calendar that cannot answer helps nobody."""
        self.store.save_user("nogoogle", {"id": "nogoogle", "email": "n@example.com"}, {})
        client = self._client("nogoogle")
        response = client.put(f"/calendar/provider?provider={GOOGLE}")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.active_calendar("nogoogle"), NATIVE)

    def test_unknown_calendar_is_refused(self):
        response = self._client("alice").put("/calendar/provider?provider=dropbox")
        self.assertEqual(response.status_code, 422)

    def test_switching_requires_sign_in(self):
        response = self._client().put(f"/calendar/provider?provider={NATIVE}")
        self.assertEqual(response.status_code, 401)

    def test_a_frontend_supplied_workspace_id_is_not_authority(self):
        """Identity comes from the session cookie, never from the request."""
        client = self._client()
        response = client.get("/calendar/status", headers={"x-user-id": "alice"})
        self.assertEqual(response.status_code, 401)

    # ---- reads follow the active calendar --------------------------------

    def test_events_come_from_the_native_calendar_once_selected(self):
        client = self._client("alice")
        client.put(f"/calendar/provider?provider={NATIVE}")
        start = local_now() + timedelta(hours=3)
        self.store.create_native_event("alice", title="Native only", start=start,
                                       end=start + timedelta(hours=1))
        body = client.get("/events").json()
        self.assertEqual([e["title"] for e in body["events"]], ["Native only"])
        self.assertEqual(body["provider"], NATIVE)

    def test_google_and_native_events_stay_separate(self):
        """Switching calendars must never blend or copy one into the other."""
        client = self._client("alice")
        start = local_now() + timedelta(hours=3)
        self.store.create_native_event("alice", title="Native only", start=start,
                                       end=start + timedelta(hours=1))
        self.services["alice"] = FakeService(list_result={"items": [{
            "id": "g1", "summary": "Google only", "status": "confirmed",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        }]})
        google_titles = [e["title"] for e in client.get("/events").json()["events"]]
        self.assertEqual(google_titles, ["Google only"])

        client.put(f"/calendar/provider?provider={NATIVE}")
        native_titles = [e["title"] for e in client.get("/events").json()["events"]]
        self.assertEqual(native_titles, ["Native only"])

    def test_expired_google_credentials_do_not_fall_back_to_native(self):
        """A silent downgrade would write events somewhere the user never chose."""
        start = local_now() + timedelta(hours=3)
        self.store.create_native_event("alice", title="Native only", start=start,
                                       end=start + timedelta(hours=1))

        def expired(user_id):
            raise HTTPException(401, "Google access expired. Please sign in again.")

        self.runtime.service_factory = expired
        response = self._client("alice").get("/events")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Native only", response.text)

    def test_a_google_read_failure_is_reported_not_substituted(self):
        """A broken Google read must say so, not quietly serve native events."""
        start = local_now() + timedelta(hours=3)
        self.store.create_native_event("alice", title="Native only", start=start,
                                       end=start + timedelta(hours=1))
        self.services["alice"] = FakeService(
            list_result={"items": []},
            list_error=HttpError(FakeResponse(500), b"server error"),
        )
        response = self._client("alice").get("/events")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Google Calendar", response.json()["detail"])
        self.assertNotIn("Native only", response.text)


if __name__ == "__main__":
    unittest.main()


class ProviderSwitchDuringConfirmationTests(unittest.TestCase):
    """A pending confirmation must never land on a calendar it was not planned for."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        self.store.save_user("alice", {"id": "alice", "email": "alice@example.com"},
                             {"refresh_token": "x"})
        self.runtime = UserRuntime(
            self.store,
            lambda: Model(Planner(QueryPlan(intent="list"))),
            lambda uid: FakeService(list_result={"items": []}),
        )

    def _pending(self, provider_name):
        """Save a conversation that is waiting on a yes/no, planned elsewhere."""
        self.store.conversation("alice", "t1", create=True)
        self.store.save_conversation("alice", "t1", {
            "messages": [], "awaiting_confirmation": True, "provider": provider_name,
        })

    def test_switching_calendars_cancels_a_waiting_confirmation(self):
        self.store.set_calendar_provider("alice", GOOGLE)
        self._pending(NATIVE)
        with self.assertRaises(HTTPException) as caught:
            self.runtime.chat("alice", "t1", "yes")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("Nothing was changed", caught.exception.detail)

    def test_the_cancelled_plan_is_cleared_not_left_waiting(self):
        self.store.set_calendar_provider("alice", GOOGLE)
        self._pending(NATIVE)
        with self.assertRaises(HTTPException):
            self.runtime.chat("alice", "t1", "yes")
        saved, _ = self.store.conversation("alice", "t1")
        self.assertFalse((saved or {}).get("awaiting_confirmation"))

    def test_the_message_names_both_calendars(self):
        self.store.set_calendar_provider("alice", GOOGLE)
        self._pending(NATIVE)
        with self.assertRaises(HTTPException) as caught:
            self.runtime.chat("alice", "t1", "yes")
        self.assertIn("Task Pilot calendar", caught.exception.detail)
        self.assertIn("Google Calendar", caught.exception.detail)

    def test_an_unchanged_calendar_lets_the_confirmation_through(self):
        self.store.set_calendar_provider("alice", NATIVE)
        self._pending(NATIVE)
        # No exception: the plan still belongs to the active calendar.
        self.runtime.chat("alice", "t1", "yes")
