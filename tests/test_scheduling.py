import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.scheduling import (
    build_bulk_changes,
    find_free_slots,
    overlapping_events,
)


ZONE = ZoneInfo("Asia/Kolkata")


def event(event_id, title, start, end):
    return {
        "event_id": event_id,
        "title": title,
        "start": start,
        "end": end,
    }


class SchedulingTests(unittest.TestCase):
    def test_overlap_uses_half_open_intervals(self):
        events = [
            event(
                "busy",
                "Meeting",
                "2026-09-11T15:00:00+05:30",
                "2026-09-11T16:00:00+05:30",
            )
        ]

        conflict = overlapping_events(
            events,
            datetime(2026, 9, 11, 15, 30, tzinfo=ZONE),
            datetime(2026, 9, 11, 16, 30, tzinfo=ZONE),
        )
        adjacent = overlapping_events(
            events,
            datetime(2026, 9, 11, 16, 0, tzinfo=ZONE),
            datetime(2026, 9, 11, 17, 0, tzinfo=ZONE),
        )

        self.assertEqual([item["event_id"] for item in conflict], ["busy"])
        self.assertEqual(adjacent, [])

    def test_free_slots_merge_overlapping_busy_periods(self):
        events = [
            event(
                "a",
                "A",
                "2026-09-11T09:00:00+05:30",
                "2026-09-11T10:30:00+05:30",
            ),
            event(
                "b",
                "B",
                "2026-09-11T10:00:00+05:30",
                "2026-09-11T11:00:00+05:30",
            ),
        ]

        slots = find_free_slots(
            events,
            datetime(2026, 9, 11, 8, tzinfo=ZONE),
            datetime(2026, 9, 11, 14, tzinfo=ZONE),
            timedelta(hours=2),
        )

        self.assertEqual(slots[0]["start"], "2026-09-11T11:00:00+05:30")
        self.assertEqual(slots[0]["end"], "2026-09-11T13:00:00+05:30")

    def test_bulk_shift_preserves_duration(self):
        changes = build_bulk_changes(
            [
                event(
                    "study-1",
                    "DSA study",
                    "2026-09-10T18:00:00+05:30",
                    "2026-09-10T19:00:00+05:30",
                )
            ],
            {"intent": "bulk_update", "shift_minutes": 1440},
        )

        self.assertEqual(changes[0]["new_start"], "2026-09-11T18:00:00+05:30")
        self.assertEqual(changes[0]["new_end"], "2026-09-11T19:00:00+05:30")

    def test_bulk_move_to_target_date_keeps_each_events_local_clock(self):
        changes = build_bulk_changes(
            [
                event(
                    "study-1",
                    "DSA study",
                    "2026-09-11T18:00:00+05:30",
                    "2026-09-11T19:00:00+05:30",
                )
            ],
            {"intent": "bulk_update", "target_date": "2026-09-14"},
        )

        self.assertEqual(changes[0]["new_start"], "2026-09-14T18:00:00+05:30")
        self.assertEqual(changes[0]["new_end"], "2026-09-14T19:00:00+05:30")

    def test_long_gap_produces_multiple_alternatives(self):
        slots = find_free_slots(
            [],
            datetime(2026, 9, 11, 16, tzinfo=ZONE),
            datetime(2026, 9, 11, 21, tzinfo=ZONE),
            timedelta(hours=1),
            limit=3,
        )

        self.assertEqual(
            [slot["start"] for slot in slots],
            [
                "2026-09-11T16:00:00+05:30",
                "2026-09-11T17:00:00+05:30",
                "2026-09-11T18:00:00+05:30",
            ],
        )

    def test_bulk_delete_only_proposes_and_does_not_execute(self):
        changes = build_bulk_changes(
            [
                event(
                    "cancelled-1",
                    "Cancelled meeting",
                    "2026-09-11T18:00:00+05:30",
                    "2026-09-11T19:00:00+05:30",
                )
            ],
            {"intent": "bulk_delete"},
        )

        self.assertEqual(changes[0]["action"], "delete")
        self.assertEqual(changes[0]["event_id"], "cancelled-1")


if __name__ == "__main__":
    unittest.main()
