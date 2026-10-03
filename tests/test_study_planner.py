"""Study-plan generation, subject-wise scheduling, and progress tracking."""

import unittest
from datetime import timedelta

from app.date_utils import local_now
from app.preferences import SchedulingPreferences
from app.study_planner import (
    MAX_SESSION_MINUTES,
    MIN_SESSION_MINUTES,
    plan_sessions,
    progress,
    split_effort,
    unschedulable,
)


def _at(now, days=0, hour=9, minutes=0):
    return (now + timedelta(days=days)).replace(
        hour=hour, minute=minutes, second=0, microsecond=0
    )


def _assignment(identity, title, due, **extra):
    record = {"id": identity, "title": title, "due": due.isoformat(),
              "subject": extra.pop("subject", "General"),
              "estimated_minutes": extra.pop("estimated_minutes", 60)}
    record.update(extra)
    return record


def _event(start, end):
    return {"event_id": "busy", "start": start.isoformat(), "end": end.isoformat(),
            "status": "confirmed"}


class SplitEffortTests(unittest.TestCase):
    def test_short_work_is_one_session(self):
        self.assertEqual(split_effort(45), [45])

    def test_raises_tiny_effort_to_the_minimum(self):
        self.assertEqual(split_effort(15), [MIN_SESSION_MINUTES])

    def test_splits_long_work(self):
        self.assertEqual(split_effort(180), [90, 90])

    def test_folds_a_short_tail_into_the_previous_session(self):
        """100 minutes must not become a 90 and a stub of 10."""
        self.assertEqual(split_effort(100), [100])
        self.assertTrue(all(part >= MIN_SESSION_MINUTES for part in split_effort(100)))

    def test_keeps_a_tail_that_stands_on_its_own(self):
        self.assertEqual(split_effort(120), [90, 30])

    def test_never_exceeds_the_cap_except_for_an_absorbed_tail(self):
        for total in (60, 95, 150, 400, 900):
            parts = split_effort(total)
            self.assertTrue(all(part <= MAX_SESSION_MINUTES + MIN_SESSION_MINUTES
                                for part in parts), total)


class PlanSessionsTests(unittest.TestCase):
    def setUp(self):
        # A fixed Monday 08:00 so the plan does not drift with the clock.
        self.now = local_now().replace(hour=8, minute=0, second=0, microsecond=0)
        self.prefs = SchedulingPreferences(work_start=8, work_end=21)

    def test_schedules_an_assignment(self):
        work = [_assignment("a1", "Essay", _at(self.now, 3, 17), estimated_minutes=60)]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["assignment_id"], "a1")
        self.assertEqual(plan[0]["title"], "Study: Essay")
        self.assertEqual(plan[0]["minutes"], 60)

    def test_carries_the_subject_through(self):
        work = [_assignment("a1", "Integrals", _at(self.now, 2, 17), subject="Maths")]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        self.assertEqual(plan[0]["subject"], "Maths")

    def test_skips_finished_assignments(self):
        work = [_assignment("a1", "Done", _at(self.now, 2, 17), status="done")]
        self.assertEqual(plan_sessions(work, [], self.prefs, now=self.now), [])

    def test_never_schedules_after_the_deadline(self):
        deadline = _at(self.now, 1, 12)
        work = [_assignment("a1", "Tight", deadline, estimated_minutes=60)]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        for session in plan:
            self.assertLessEqual(session["end"], deadline.isoformat())

    def test_never_schedules_in_the_past(self):
        work = [_assignment("a1", "Essay", _at(self.now, 2, 17))]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        for session in plan:
            self.assertGreaterEqual(session["start"], self.now.isoformat())

    def test_avoids_existing_calendar_events(self):
        busy = _event(_at(self.now, 0, 8), _at(self.now, 0, 20))
        work = [_assignment("a1", "Essay", _at(self.now, 2, 20), estimated_minutes=60)]
        plan = plan_sessions(work, [busy], self.prefs, now=self.now)
        self.assertTrue(plan)
        # Nothing may land inside the occupied block on day zero.
        for session in plan:
            self.assertFalse(
                busy["start"] <= session["start"] < busy["end"],
                f"session {session['start']} overlaps the busy block",
            )

    def test_sessions_do_not_overlap_each_other(self):
        work = [
            _assignment("a1", "One", _at(self.now, 4, 20), estimated_minutes=180),
            _assignment("a2", "Two", _at(self.now, 4, 20), estimated_minutes=180),
        ]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        ordered = sorted(plan, key=lambda s: s["start"])
        for earlier, later in zip(ordered, ordered[1:]):
            self.assertLessEqual(earlier["end"], later["start"])

    def test_more_urgent_work_is_scheduled_first(self):
        work = [
            _assignment("far", "Far", _at(self.now, 6, 20), estimated_minutes=60),
            _assignment("near", "Near", _at(self.now, 1, 20), estimated_minutes=60,
                        priority="urgent"),
        ]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        self.assertEqual(plan[0]["assignment_id"], "near")

    def test_respects_remaining_effort(self):
        work = [_assignment("a1", "Essay", _at(self.now, 5, 20), estimated_minutes=180)]
        plan = plan_sessions(work, [], self.prefs, now=self.now,
                             completed_minutes={"a1": 120})
        self.assertEqual(sum(s["minutes"] for s in plan), 60)

    def test_nothing_left_to_do_schedules_nothing(self):
        work = [_assignment("a1", "Essay", _at(self.now, 5, 20), estimated_minutes=60)]
        plan = plan_sessions(work, [], self.prefs, now=self.now,
                             completed_minutes={"a1": 60})
        self.assertEqual(plan, [])

    def test_caps_sessions_per_day(self):
        work = [_assignment("a1", "Huge", _at(self.now, 9, 20), estimated_minutes=900)]
        plan = plan_sessions(work, [], self.prefs, now=self.now, days=9)
        by_day = {}
        for session in plan:
            by_day[session["start"][:10]] = by_day.get(session["start"][:10], 0) + 1
        self.assertTrue(all(count <= 4 for count in by_day.values()), by_day)

    def test_returns_sessions_in_chronological_order(self):
        work = [
            _assignment("a1", "One", _at(self.now, 3, 20), estimated_minutes=120),
            _assignment("a2", "Two", _at(self.now, 2, 20), estimated_minutes=120),
        ]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        self.assertEqual([s["start"] for s in plan], sorted(s["start"] for s in plan))

    def test_honours_a_preferred_study_start(self):
        prefs = SchedulingPreferences(work_start=8, work_end=21, study_start=16)
        work = [_assignment("a1", "DSA practice", _at(self.now, 2, 20))]
        plan = plan_sessions(work, [], prefs, now=self.now)
        self.assertTrue(plan)
        self.assertGreaterEqual(int(plan[0]["start"][11:13]), 16)

    def test_accepts_preferences_as_a_plain_dict(self):
        work = [_assignment("a1", "Essay", _at(self.now, 2, 17))]
        plan = plan_sessions(work, [], {"work_start": 8, "work_end": 21}, now=self.now)
        self.assertTrue(plan)


class UnschedulableTests(unittest.TestCase):
    def setUp(self):
        self.now = local_now().replace(hour=8, minute=0, second=0, microsecond=0)
        self.prefs = SchedulingPreferences(work_start=8, work_end=21)

    def test_reports_work_that_does_not_fit_before_its_deadline(self):
        """A full day of effort due in two hours cannot be hidden."""
        work = [_assignment("a1", "Impossible", _at(self.now, 0, 10),
                            estimated_minutes=600)]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        stranded = unschedulable(work, plan, now=self.now)
        self.assertEqual(len(stranded), 1)
        self.assertEqual(stranded[0]["assignment_id"], "a1")
        self.assertGreater(stranded[0]["unscheduled_minutes"], 0)

    def test_silent_about_work_that_fits(self):
        work = [_assignment("a1", "Fine", _at(self.now, 5, 20), estimated_minutes=60)]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        self.assertEqual(unschedulable(work, plan, now=self.now), [])

    def test_flags_overdue_work(self):
        work = [_assignment("a1", "Late", self.now - timedelta(days=2),
                            estimated_minutes=120)]
        plan = plan_sessions(work, [], self.prefs, now=self.now)
        stranded = unschedulable(work, plan, now=self.now)
        self.assertTrue(stranded[0]["overdue"])


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = local_now()
        self.work = [
            _assignment("a1", "Essay", self.now + timedelta(days=2), subject="English"),
            _assignment("a2", "Integrals", self.now + timedelta(days=3), subject="Maths"),
            _assignment("a3", "Algebra", self.now + timedelta(days=4), subject="Maths",
                        status="done"),
        ]

    def test_groups_by_subject(self):
        report = progress(self.work, [])
        names = [row["subject"] for row in report["subjects"]]
        self.assertEqual(names, ["English", "Maths"])

    def test_counts_completed_assignments(self):
        report = progress(self.work, [])
        maths = next(r for r in report["subjects"] if r["subject"] == "Maths")
        self.assertEqual(maths["assignments"], 2)
        self.assertEqual(maths["completed_assignments"], 1)

    def test_studied_minutes_only_count_finished_sessions(self):
        sessions = [
            {"subject": "Maths", "minutes": 60, "status": "done"},
            {"subject": "Maths", "minutes": 60, "status": "planned"},
        ]
        report = progress(self.work, sessions)
        maths = next(r for r in report["subjects"] if r["subject"] == "Maths")
        self.assertEqual(maths["planned_minutes"], 120)
        self.assertEqual(maths["studied_minutes"], 60)
        self.assertEqual(maths["completion"], 50.0)

    def test_overall_totals(self):
        sessions = [
            {"subject": "Maths", "minutes": 60, "status": "done"},
            {"subject": "English", "minutes": 60, "status": "done"},
            {"subject": "English", "minutes": 60, "status": "planned"},
        ]
        report = progress(self.work, sessions)
        self.assertEqual(report["planned_minutes"], 180)
        self.assertEqual(report["studied_minutes"], 120)
        self.assertEqual(report["completion"], 66.7)

    def test_no_sessions_is_zero_not_a_crash(self):
        report = progress(self.work, [])
        self.assertEqual(report["completion"], 0.0)
        self.assertEqual(report["studied_minutes"], 0)


if __name__ == "__main__":
    unittest.main()
