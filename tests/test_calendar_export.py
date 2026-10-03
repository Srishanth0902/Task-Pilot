"""Calendar export: ICS structure and CSV contents, plus the HTTP routes."""

import csv
import io
import tempfile
import unittest
from datetime import timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.calendar_export import to_csv, to_ics
from app.date_utils import local_now
from app.graph_agent import QueryPlan
from app.multiuser import COOKIE, UserRuntime, create_multiuser_app
from app.user_store import UserStore
from tests.test_advanced_agent import Planner
from tests.test_calendar_service import FakeService
from tests.test_multiuser import Model


def _unfold(text):
    """Reverse RFC 5545 line folding so values can be asserted on."""
    lines = []
    for raw in text.split("\r\n"):
        if raw.startswith(" ") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return [line for line in lines if line]


class IcsStructureTests(unittest.TestCase):
    def setUp(self):
        self.now = local_now()
        self.events = [{
            "event_id": "e1", "title": "Lecture",
            "start": (self.now + timedelta(hours=2)).isoformat(),
            "end": (self.now + timedelta(hours=3)).isoformat(),
            "location": "Room 4",
        }]
        self.assignments = [{
            "id": "a1", "title": "Essay", "subject": "English", "priority": "high",
            "status": "todo", "due": (self.now + timedelta(days=2)).isoformat(),
        }]
        self.sessions = [{
            "id": "s1", "title": "Study: Essay", "subject": "English",
            "start": (self.now + timedelta(days=1)).isoformat(),
            "end": (self.now + timedelta(days=1, hours=1)).isoformat(),
        }]

    def test_calendar_envelope(self):
        text = to_ics(self.events, self.assignments, self.sessions)
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        self.assertIn("VERSION:2.0", text)

    def test_uses_crlf_line_endings(self):
        """Clients that follow RFC 5545 strictly reject bare newlines."""
        text = to_ics(self.events)
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_every_block_is_balanced(self):
        text = to_ics(self.events, self.assignments, self.sessions)
        self.assertEqual(text.count("BEGIN:VEVENT"), 3)
        self.assertEqual(text.count("BEGIN:VEVENT"), text.count("END:VEVENT"))

    def test_events_carry_their_details(self):
        lines = _unfold(to_ics(self.events))
        self.assertIn("SUMMARY:Lecture", lines)
        self.assertIn("LOCATION:Room 4", lines)
        self.assertTrue(any(line.startswith("DTSTART:") for line in lines))

    def test_deadlines_appear_as_due_entries(self):
        lines = _unfold(to_ics([], self.assignments))
        self.assertIn("SUMMARY:Due: Essay", lines)
        description = next(line for line in lines if line.startswith("DESCRIPTION:"))
        self.assertIn("English", description)
        self.assertIn("high", description)

    def test_study_sessions_are_included(self):
        lines = _unfold(to_ics([], [], self.sessions))
        self.assertIn("SUMMARY:Study: Essay", lines)

    def test_all_day_events_use_date_values(self):
        entry = [{"event_id": "e1", "title": "Holiday",
                  "start": "2026-10-05", "end": "2026-10-06"}]
        self.assertIn("DTSTART;VALUE=DATE:20261005", to_ics(entry))

    def test_special_characters_are_escaped(self):
        entry = [{"event_id": "e1", "title": "Maths, Physics; and more",
                  "start": (self.now + timedelta(hours=1)).isoformat(),
                  "end": (self.now + timedelta(hours=2)).isoformat()}]
        lines = _unfold(to_ics(entry))
        summary = next(line for line in lines if line.startswith("SUMMARY:"))
        self.assertIn("\\,", summary)
        self.assertIn("\\;", summary)

    def test_newlines_in_notes_do_not_break_the_file(self):
        work = [{"id": "a1", "title": "Essay", "notes": "line one\nline two",
                 "due": (self.now + timedelta(days=1)).isoformat()}]
        text = to_ics([], work)
        self.assertIn("\\n", text)
        # One DESCRIPTION property, not two lines masquerading as properties.
        self.assertEqual(len([l for l in _unfold(text) if l.startswith("DESCRIPTION:")]), 1)

    def test_long_titles_are_folded_and_recoverable(self):
        title = "Revision " * 30
        entry = [{"event_id": "e1", "title": title.strip(),
                  "start": (self.now + timedelta(hours=1)).isoformat(),
                  "end": (self.now + timedelta(hours=2)).isoformat()}]
        text = to_ics(entry)
        self.assertTrue(all(len(line.encode()) <= 75
                            for line in text.split("\r\n") if line))
        self.assertIn("SUMMARY:" + title.strip(), _unfold(text))

    def test_folding_preserves_non_ascii(self):
        entry = [{"event_id": "e1", "title": "Révision " * 20,
                  "start": (self.now + timedelta(hours=1)).isoformat(),
                  "end": (self.now + timedelta(hours=2)).isoformat()}]
        text = to_ics(entry)
        self.assertIn("Révision", "".join(_unfold(text)))

    def test_empty_export_is_still_valid(self):
        text = to_ics([], [], [])
        self.assertIn("BEGIN:VCALENDAR", text)
        self.assertNotIn("BEGIN:VEVENT", text)

    def test_entries_without_a_start_are_skipped(self):
        self.assertNotIn("BEGIN:VEVENT", to_ics([{"event_id": "e1", "title": "No time"}]))


class CsvExportTests(unittest.TestCase):
    def setUp(self):
        self.now = local_now()

    def test_header_and_rows(self):
        text = to_csv(
            [{"title": "Lecture", "start": "s", "end": "e", "location": "Room 4"}],
            [{"title": "Essay", "subject": "English", "due": "d", "priority": "high",
              "status": "todo"}],
            [{"title": "Study: Essay", "subject": "English", "start": "s", "end": "e",
              "status": "planned"}],
        )
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][0], "type")
        self.assertEqual([row[0] for row in rows[1:]], ["event", "assignment", "study"])

    def test_commas_in_titles_do_not_shift_columns(self):
        text = to_csv([{"title": "Maths, Physics", "start": "s", "end": "e"}])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[1][1], "Maths, Physics")
        self.assertEqual(len(rows[1]), len(rows[0]))

    def test_empty_export_has_only_a_header(self):
        rows = list(csv.reader(io.StringIO(to_csv())))
        self.assertEqual(len(rows), 1)


class ExportRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        for uid in ("alice", "bob"):
            self.store.save_user(uid, {"id": uid, "email": uid + "@example.com"},
                                 {"refresh_token": "x"})
        runtime = UserRuntime(
            self.store,
            lambda: Model(Planner(QueryPlan(intent="list"))),
            lambda uid: FakeService(list_result={"items": []}),
        )
        self.app = create_multiuser_app(self.store, runtime, origin="http://127.0.0.1:5173")
        self.alice = self._client("alice")
        self.bob = self._client("bob")

    def _client(self, user_id):
        client = TestClient(self.app)
        client.cookies.set(COOKIE, self.store.session(user_id))
        client.headers.update({"origin": "http://127.0.0.1:5173", "x-task-pilot": "1"})
        return client

    def test_requires_sign_in(self):
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.get("/export/calendar.ics").status_code, 401)
        self.assertEqual(anonymous.get("/export/schedule.csv").status_code, 401)

    def test_ics_download(self):
        self.store.save_assignment("alice", {
            "title": "Essay", "due": (local_now() + timedelta(days=2)).isoformat()})
        response = self.alice.get("/export/calendar.ics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/calendar", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("BEGIN:VCALENDAR", response.text)
        self.assertIn("Due: Essay", response.text)

    def test_csv_download(self):
        self.store.save_assignment("alice", {
            "title": "Essay", "due": (local_now() + timedelta(days=2)).isoformat()})
        response = self.alice.get("/export/schedule.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("Essay", response.text)

    def test_exports_only_your_own_schedule(self):
        self.store.save_assignment("alice", {
            "title": "Alice secret", "due": (local_now() + timedelta(days=2)).isoformat()})
        self.assertNotIn("Alice secret", self.bob.get("/export/calendar.ics").text)
        self.assertNotIn("Alice secret", self.bob.get("/export/schedule.csv").text)


if __name__ == "__main__":
    unittest.main()
