import unittest
from datetime import datetime

from pydantic import ValidationError

from app.schemas import CreateEventInput, SearchEventInput, UpdateEventInput


class SchemaTests(unittest.TestCase):
    def test_create_defaults_to_one_hour_and_localises(self):
        value = CreateEventInput(
            title="ML study", start_time="2026-08-25T18:00:00"
        )

        self.assertEqual(value.start_time.isoformat(), "2026-08-25T18:00:00+05:30")
        self.assertEqual((value.end_time - value.start_time).total_seconds(), 3600)

    def test_create_rejects_reverse_range(self):
        with self.assertRaises(ValidationError):
            CreateEventInput(
                title="bad",
                start_time="2026-08-25T19:00:00+05:30",
                end_time="2026-08-25T18:00:00+05:30",
            )

    def test_update_requires_a_change(self):
        with self.assertRaises(ValidationError):
            UpdateEventInput(event_id="evt-1")

    def test_update_requires_start_and_end_together(self):
        with self.assertRaises(ValidationError):
            UpdateEventInput(
                event_id="evt-1", start_time=datetime(2026, 8, 25, 18)
            )

    def test_search_validates_result_limit(self):
        with self.assertRaises(ValidationError):
            SearchEventInput(query="DSA", max_results=0)
