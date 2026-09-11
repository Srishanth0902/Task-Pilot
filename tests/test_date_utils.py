import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.date_utils import (
    format_local_datetime,
    format_local_range,
    infer_local_time_window,
    parse_datetime_expression,
)


class NaturalDateTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Asia/Kolkata")
        self.now = datetime(2026, 8, 24, 12, 30, tzinfo=self.zone)  # Monday

    def test_tomorrow_at_6_pm(self):
        result = parse_datetime_expression("tomorrow at 6 PM", now=self.now)
        self.assertEqual(result, datetime(2026, 8, 25, 18, 0, tzinfo=self.zone))

    def test_tomorrow_at_6_without_period_is_six_local(self):
        result = parse_datetime_expression("tomorrow at 6", now=self.now)
        self.assertEqual(result, datetime(2026, 8, 25, 6, 0, tzinfo=self.zone))

    def test_next_monday_is_not_today(self):
        result = parse_datetime_expression("next Monday at 9 AM", now=self.now)
        self.assertEqual(result, datetime(2026, 8, 31, 9, 0, tzinfo=self.zone))

    def test_bare_weekday_is_the_next_future_occurrence(self):
        result = parse_datetime_expression("Friday at 6 PM", now=self.now)
        self.assertEqual(result, datetime(2026, 8, 28, 18, 0, tzinfo=self.zone))

    def test_bare_weekday_can_mean_later_today(self):
        friday = datetime(2026, 8, 28, 12, 0, tzinfo=self.zone)
        result = parse_datetime_expression("Friday at 6 PM", now=friday)
        self.assertEqual(result, datetime(2026, 8, 28, 18, 0, tzinfo=self.zone))

    def test_bare_weekday_rolls_over_if_time_has_passed(self):
        friday = datetime(2026, 8, 28, 20, 0, tzinfo=self.zone)
        result = parse_datetime_expression("Friday at 6 PM", now=friday)
        self.assertEqual(result, datetime(2026, 9, 4, 18, 0, tzinfo=self.zone))

    def test_number_words_in_relative_hours(self):
        result = parse_datetime_expression("in two hours", now=self.now)
        self.assertEqual(result, datetime(2026, 8, 24, 14, 30, tzinfo=self.zone))

    def test_next_week_preserves_local_time(self):
        result = parse_datetime_expression("next week", now=self.now)
        self.assertEqual(result, datetime(2026, 8, 31, 12, 30, tzinfo=self.zone))

    def test_naive_iso_is_localised(self):
        result = parse_datetime_expression("2026-08-25T18:00:00", now=self.now)
        self.assertEqual(result.isoformat(), "2026-08-25T18:00:00+05:30")

    def test_friendly_output_converts_utc_and_labels_ist(self):
        self.assertEqual(
            format_local_datetime("2026-09-11T01:30:00Z"),
            "Friday, 11 September 2026 at 7:00 AM IST",
        )
        self.assertEqual(
            format_local_range(
                "2026-09-11T01:30:00Z", "2026-09-11T02:30:00Z"
            ),
            "Friday, 11 September 2026, 7:00 AM–8:00 AM IST",
        )

    def test_tomorrow_after_6_pm_becomes_an_ist_window(self):
        start, end = infer_local_time_window(
            "Show slots tomorrow after 6 PM", now=self.now
        )

        self.assertEqual(start.isoformat(), "2026-08-25T18:00:00+05:30")
        self.assertEqual(end.isoformat(), "2026-08-25T21:00:00+05:30")

    def test_availability_range_requires_a_day(self):
        self.assertIsNone(infer_local_time_window("after 6 PM", now=self.now))

    def test_unsupported_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported date expression"):
            parse_datetime_expression("sometime after lunch", now=self.now)
