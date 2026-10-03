"""Assignment model and storage, including per-account isolation."""

import tempfile
import unittest
from datetime import timedelta

from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.assignments import Assignment, sort_key
from app.date_utils import local_now
from app.user_store import UserStore


def _store():
    temp = tempfile.TemporaryDirectory()
    return UserStore(temp.name, Fernet.generate_key()), temp


def _due(hours):
    return (local_now() + timedelta(hours=hours)).isoformat()


class AssignmentModelTests(unittest.TestCase):
    def test_accepts_natural_language_deadline(self):
        work = Assignment(title="Essay", due="tomorrow at 5 PM")
        self.assertIsNotNone(work.due.tzinfo)

    def test_defaults(self):
        work = Assignment(title="Lab report", due=_due(24))
        self.assertEqual(work.priority, "medium")
        self.assertEqual(work.status, "todo")
        self.assertEqual(work.estimated_minutes, 60)

    def test_rejects_unknown_priority(self):
        with self.assertRaises(ValidationError):
            Assignment(title="X", due=_due(1), priority="screaming")

    def test_rejects_empty_title(self):
        with self.assertRaises(ValidationError):
            Assignment(title="  ", due=_due(1))

    def test_collapses_whitespace(self):
        work = Assignment(title="  Data   Structures  ", due=_due(5))
        self.assertEqual(work.title, "Data Structures")

    def test_overdue_only_when_unfinished(self):
        late = Assignment(title="Late", due=_due(-5))
        self.assertTrue(late.overdue())
        late.status = "done"
        self.assertFalse(late.overdue())

    def test_urgency_rises_as_deadline_approaches(self):
        soon = Assignment(title="Soon", due=_due(2))
        later = Assignment(title="Later", due=_due(200))
        self.assertGreater(soon.urgency(), later.urgency())

    def test_priority_breaks_ties(self):
        high = Assignment(title="A", due=_due(10), priority="urgent")
        low = Assignment(title="B", due=_due(10), priority="low")
        self.assertGreater(high.urgency(), low.urgency())

    def test_finished_work_has_no_urgency(self):
        done = Assignment(title="Done", due=_due(1), status="done")
        self.assertEqual(done.urgency(), 0.0)

    def test_long_overdue_does_not_dominate_forever(self):
        """An assignment forgotten months ago must not outrank everything."""
        ancient = Assignment(title="Ancient", due=_due(-24 * 90), priority="low")
        imminent = Assignment(title="Now", due=_due(1), priority="urgent")
        self.assertGreater(imminent.urgency(), ancient.urgency())

    def test_sort_key_puts_done_last(self):
        records = [
            {"status": "done", "due": 1, "title": "finished"},
            {"status": "todo", "due": 9, "title": "pending"},
        ]
        self.assertEqual([r["title"] for r in sorted(records, key=sort_key)],
                         ["pending", "finished"])


class AssignmentStoreTests(unittest.TestCase):
    def setUp(self):
        self.store, self.temp = _store()
        self.addCleanup(self.temp.cleanup)

    def test_round_trip(self):
        saved = self.store.save_assignment("alice", {
            "title": "OS assignment", "subject": "Operating Systems",
            "due": _due(48), "priority": "high", "estimated_minutes": 180,
        })
        self.assertEqual(saved["title"], "OS assignment")
        self.assertEqual(saved["priority"], "high")
        fetched = self.store.assignment("alice", saved["id"])
        self.assertEqual(fetched["subject"], "Operating Systems")
        self.assertEqual(fetched["estimated_minutes"], 180)

    def test_listed_in_deadline_order(self):
        for hours, title in ((72, "third"), (5, "first"), (30, "second")):
            self.store.save_assignment("alice", {"title": title, "due": _due(hours)})
        titles = [row["title"] for row in self.store.assignments("alice")]
        self.assertEqual(titles, ["first", "second", "third"])

    def test_can_hide_finished_work(self):
        self.store.save_assignment("alice", {"title": "Open", "due": _due(5)})
        self.store.save_assignment("alice", {"title": "Shut", "due": _due(6), "status": "done"})
        open_only = self.store.assignments("alice", include_done=False)
        self.assertEqual([row["title"] for row in open_only], ["Open"])

    def test_update_preserves_identity_and_created(self):
        first = self.store.save_assignment("alice", {"title": "Draft", "due": _due(10)})
        second = self.store.save_assignment(
            "alice", {"title": "Final", "due": _due(10), "status": "in_progress"},
            assignment_id=first["id"],
        )
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["created"], first["created"])
        self.assertEqual(second["title"], "Final")
        self.assertEqual(len(self.store.assignments("alice")), 1)

    def test_accounts_are_isolated(self):
        mine = self.store.save_assignment("alice", {"title": "Private", "due": _due(3)})
        self.assertEqual(self.store.assignments("bob"), [])
        with self.assertRaises(PermissionError):
            self.store.assignment("bob", mine["id"])
        self.assertFalse(self.store.delete_assignment("bob", mine["id"]))
        # Still intact for its owner after the failed cross-account attempts.
        self.assertEqual(self.store.assignment("alice", mine["id"])["title"], "Private")

    def test_cannot_overwrite_another_account(self):
        mine = self.store.save_assignment("alice", {"title": "Mine", "due": _due(3)})
        with self.assertRaises(PermissionError):
            self.store.save_assignment(
                "bob", {"title": "Hijacked", "due": _due(3)}, assignment_id=mine["id"]
            )
        self.assertEqual(self.store.assignment("alice", mine["id"])["title"], "Mine")

    def test_delete(self):
        row = self.store.save_assignment("alice", {"title": "Temp", "due": _due(2)})
        self.assertTrue(self.store.delete_assignment("alice", row["id"]))
        self.assertFalse(self.store.delete_assignment("alice", row["id"]))
        self.assertEqual(self.store.assignments("alice"), [])

    def test_titles_are_not_stored_in_plaintext(self):
        self.store.save_assignment("alice", {"title": "Confidential thesis", "due": _due(5)})
        raw = (self.store.directory / "users.sqlite3").read_bytes()
        self.assertNotIn(b"Confidential thesis", raw)

    def test_due_sweep_spans_accounts_and_skips_done(self):
        now = local_now().timestamp()
        self.store.save_assignment("alice", {"title": "Soon", "due": _due(2)})
        self.store.save_assignment("bob", {"title": "Also soon", "due": _due(3)})
        self.store.save_assignment("alice", {"title": "Finished", "due": _due(2), "status": "done"})
        self.store.save_assignment("alice", {"title": "Far off", "due": _due(400)})
        found = self.store.assignments_due_between(now, now + 24 * 3600)
        self.assertEqual(
            sorted((uid, row["title"]) for uid, row in found),
            [("alice", "Soon"), ("bob", "Also soon")],
        )

    def test_user_ids(self):
        self.store.save_user("alice", {"email": "a@example.com"}, {"refresh_token": "x"})
        self.store.save_user("bob", {"email": "b@example.com"}, {"refresh_token": "y"})
        self.assertEqual(sorted(self.store.user_ids()), ["alice", "bob"])


class ReminderClaimTests(unittest.TestCase):
    def setUp(self):
        self.store, self.temp = _store()
        self.addCleanup(self.temp.cleanup)

    def test_claimed_once_only(self):
        self.assertTrue(self.store.reminder_claim("alice", "event:abc:60"))
        self.assertFalse(self.store.reminder_claim("alice", "event:abc:60"))

    def test_claims_are_per_account(self):
        self.assertTrue(self.store.reminder_claim("alice", "event:abc:60"))
        self.assertTrue(self.store.reminder_claim("bob", "event:abc:60"))

    def test_distinct_lead_times_claim_separately(self):
        self.assertTrue(self.store.reminder_claim("alice", "event:abc:60"))
        self.assertTrue(self.store.reminder_claim("alice", "event:abc:1440"))


if __name__ == "__main__":
    unittest.main()
