"""Email reminders: what goes out, when, what it says, and sending it once."""

import tempfile
import unittest
from datetime import timedelta

from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.date_utils import local_now
from app.preferences import SchedulingPreferences
from app.reminders import (
    DEFAULT_DEADLINE_LEADS,
    DEFAULT_EVENT_LEADS,
    ReminderService,
    SmtpMailer,
    compose,
    due_reminders,
)
from app.user_store import UserStore


class Mailer:
    """Records what would have been sent instead of touching the network."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    @property
    def configured(self):
        return True

    def send(self, to, subject, body):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True


def _event(identity, title, start, end=None, **extra):
    start_dt = start
    record = {
        "event_id": identity,
        "title": title,
        "start": start_dt.isoformat(),
        "end": (end or start_dt + timedelta(hours=1)).isoformat(),
    }
    record.update(extra)
    return record


class DueReminderTests(unittest.TestCase):
    def setUp(self):
        self.now = local_now()

    def test_event_reminder_fires_at_its_lead_time(self):
        start = self.now + timedelta(minutes=60)
        found = due_reminders([_event("e1", "Stand-up", start)], [], now=self.now)
        self.assertEqual([r["key"] for r in found], ["event:e1:60"])

    def test_nothing_fires_too_early(self):
        start = self.now + timedelta(hours=10)
        self.assertEqual(due_reminders([_event("e1", "Later", start)], [], now=self.now), [])

    def test_stale_reminders_are_not_replayed(self):
        """A service down for a day must not fire a lead time it slept through.

        With a 48-hour lead and a deadline 24 hours away, the send moment was a
        day ago — far outside the grace window — so it is dropped rather than
        delivered late.
        """
        due = (self.now + timedelta(hours=24)).isoformat()
        work = [{"id": "a1", "title": "Essay", "due": due, "status": "todo"}]
        found = due_reminders([], work, now=self.now,
                              preferences={"deadline_reminder_minutes": [48 * 60]})
        self.assertEqual(found, [])

    def test_catches_up_within_the_grace_window(self):
        """A sweep running late still delivers a reminder from minutes ago."""
        start = self.now + timedelta(minutes=35)
        found = due_reminders([_event("e1", "Soon", start)], [], now=self.now)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["lead_minutes"], 60)

    def test_past_events_never_remind(self):
        start = self.now - timedelta(minutes=30)
        self.assertEqual(due_reminders([_event("e1", "Gone", start)], [], now=self.now), [])

    def test_all_day_events_are_skipped(self):
        entry = {"event_id": "e1", "title": "Holiday", "start": "2026-10-05", "end": "2026-10-06"}
        self.assertEqual(due_reminders([entry], [], now=self.now), [])

    def test_malformed_times_are_ignored(self):
        entry = {"event_id": "e1", "title": "Broken", "start": "not-a-time", "end": None}
        self.assertEqual(due_reminders([entry], [], now=self.now), [])

    def test_deadline_reminder(self):
        due = (self.now + timedelta(minutes=24 * 60)).isoformat()
        work = [{"id": "a1", "title": "Essay", "due": due, "status": "todo"}]
        found = due_reminders([], work, now=self.now)
        self.assertEqual([r["key"] for r in found], ["deadline:a1:1440"])

    def test_finished_assignments_do_not_remind(self):
        due = (self.now + timedelta(minutes=24 * 60)).isoformat()
        work = [{"id": "a1", "title": "Essay", "due": due, "status": "done"}]
        self.assertEqual(due_reminders([], work, now=self.now), [])

    def test_one_reminder_per_item_per_sweep(self):
        """Overlapping lead times must not produce two mails at once."""
        start = self.now + timedelta(minutes=60)
        found = due_reminders([_event("e1", "Stand-up", start)], [], now=self.now,
                              preferences={"event_reminder_minutes": [60, 55]})
        self.assertEqual(len(found), 1)

    def test_custom_lead_times_are_honoured(self):
        start = self.now + timedelta(minutes=15)
        found = due_reminders([_event("e1", "Soon", start)], [], now=self.now,
                              preferences={"event_reminder_minutes": [15]})
        self.assertEqual(found[0]["lead_minutes"], 15)

    def test_results_are_ordered_by_when_they_happen(self):
        soon = _event("e1", "Soon", self.now + timedelta(minutes=60))
        due = (self.now + timedelta(minutes=24 * 60)).isoformat()
        work = [{"id": "a1", "title": "Essay", "due": due, "status": "todo"}]
        found = due_reminders([soon], work, now=self.now)
        self.assertEqual([r["kind"] for r in found], ["event", "deadline"])

    def test_defaults_are_sane(self):
        self.assertIn(60, DEFAULT_EVENT_LEADS)
        self.assertIn(24 * 60, DEFAULT_DEADLINE_LEADS)


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.now = local_now()

    def test_deadline_subject_and_body(self):
        found = due_reminders([], [{
            "id": "a1", "title": "Essay", "subject": "English", "priority": "high",
            "due": (self.now + timedelta(minutes=24 * 60)).isoformat(), "status": "todo",
        }], now=self.now)
        subject, body = compose(found[0], "Alice")
        self.assertIn("Essay", subject)
        self.assertIn("tomorrow", subject)
        self.assertIn("Hello Alice", body)
        self.assertIn("English", body)
        self.assertIn("high", body)

    def test_event_body_includes_location(self):
        found = due_reminders(
            [_event("e1", "Lecture", self.now + timedelta(minutes=60), location="Room 4")],
            [], now=self.now,
        )
        subject, body = compose(found[0])
        self.assertIn("Lecture", subject)
        self.assertIn("Room 4", body)

    def test_greeting_without_a_name(self):
        found = due_reminders([_event("e1", "Lecture", self.now + timedelta(minutes=60))],
                              [], now=self.now)
        _, body = compose(found[0])
        self.assertIn("Hello,", body)


class ReminderServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        self.store.save_user("alice", {"id": "alice", "email": "alice@example.com",
                                       "name": "Alice"}, {"refresh_token": "x"})
        self.store.save_user("bob", {"id": "bob", "email": "bob@example.com"},
                             {"refresh_token": "y"})
        self.now = local_now()
        self.mailer = Mailer()

    def _service(self, events=None):
        return ReminderService(self.store, self.mailer,
                               event_reader=lambda uid, hours: events or [])

    def _deadline(self, user_id, minutes_ahead=24 * 60):
        return self.store.save_assignment(user_id, {
            "title": "Essay", "due": (self.now + timedelta(minutes=minutes_ahead)).isoformat(),
        })

    def test_sends_a_deadline_reminder(self):
        self._deadline("alice")
        result = self._service().sweep(now=self.now)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(self.mailer.sent[0]["to"], "alice@example.com")
        self.assertIn("Essay", self.mailer.sent[0]["subject"])

    def test_never_sends_the_same_reminder_twice(self):
        self._deadline("alice")
        service = self._service()
        service.sweep(now=self.now)
        service.sweep(now=self.now)
        self.assertEqual(len(self.mailer.sent), 1)

    def test_each_account_gets_its_own(self):
        self._deadline("alice")
        self._deadline("bob")
        self._service().sweep(now=self.now)
        self.assertEqual(sorted(m["to"] for m in self.mailer.sent),
                         ["alice@example.com", "bob@example.com"])

    def test_accounts_can_turn_reminders_off(self):
        self._deadline("alice")
        self.store.save_preferences("alice", {"email_reminders": False})
        result = self._service().sweep(now=self.now)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["skipped_accounts"], 1)

    def test_event_reminders_use_the_injected_reader(self):
        events = [_event("e1", "Lecture", self.now + timedelta(minutes=60))]
        result = self._service(events).sweep(now=self.now)
        self.assertEqual(result["sent"], 2)  # one per account
        self.assertTrue(all("Lecture" in m["subject"] for m in self.mailer.sent))

    def test_a_broken_calendar_still_lets_deadlines_through(self):
        """Deadline reminders do not need Google, so they must survive it."""
        def broken(user_id, hours):
            raise RuntimeError("calendar down")

        self._deadline("alice")
        service = ReminderService(self.store, self.mailer, event_reader=broken)
        self.assertEqual(service.sweep(now=self.now)["sent"], 1)

    def test_send_failures_are_counted_not_raised(self):
        self._deadline("alice")
        service = ReminderService(self.store, Mailer(fail=True),
                                  event_reader=lambda uid, hours: [])
        result = service.sweep(now=self.now)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["sent"], 0)

    def test_accounts_without_an_address_are_skipped(self):
        self.store.save_user("ghost", {"id": "ghost"}, {"refresh_token": "z"})
        self._deadline("ghost")
        self.assertEqual(self._service().sweep(now=self.now)["sent"], 0)


class MailerConfigTests(unittest.TestCase):
    def test_unconfigured_mailer_refuses_to_send(self):
        mailer = SmtpMailer(host="", sender="")
        self.assertFalse(mailer.configured)
        with self.assertRaises(RuntimeError):
            mailer.send("a@example.com", "s", "b")

    def test_configured_when_host_and_sender_present(self):
        mailer = SmtpMailer(host="smtp.example.com", sender="bot@example.com")
        self.assertTrue(mailer.configured)


class ReminderPreferenceTests(unittest.TestCase):
    def test_defaults_on(self):
        self.assertTrue(SchedulingPreferences().email_reminders)

    def test_lead_times_are_sorted_and_deduplicated(self):
        prefs = SchedulingPreferences(event_reminder_minutes=[60, 1440, 60])
        self.assertEqual(prefs.event_reminder_minutes, [1440, 60])

    def test_rejects_absurd_lead_times(self):
        with self.assertRaises(ValidationError):
            SchedulingPreferences(event_reminder_minutes=[1])
        with self.assertRaises(ValidationError):
            SchedulingPreferences(deadline_reminder_minutes=[99999])


if __name__ == "__main__":
    unittest.main()


class ReminderWorkerTests(unittest.TestCase):
    """The command-line entry point used by cron or a container sidecar."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UserStore(self.temp.name, Fernet.generate_key())
        self.store.save_user("alice", {"id": "alice", "email": "alice@example.com"},
                             {"refresh_token": "x"})

    def test_refuses_to_run_without_smtp(self):
        from app.reminder_worker import main
        self.assertEqual(main(["--once", "--no-calendar"]), 1)

    def test_single_sweep_sends_and_exits(self):
        from app.reminder_worker import build_service

        self.store.save_assignment("alice", {
            "title": "Essay",
            "due": (local_now() + timedelta(minutes=24 * 60)).isoformat(),
        })
        mailer = Mailer()
        service = build_service(self.store, mailer, with_calendar=False)
        result = service.sweep()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(mailer.sent[0]["to"], "alice@example.com")

    def test_deadline_sweep_needs_no_calendar(self):
        from app.reminder_worker import build_service
        service = build_service(self.store, Mailer(), with_calendar=False)
        self.assertIsNone(service.event_reader)
