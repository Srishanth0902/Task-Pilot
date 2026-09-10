import unittest

from pydantic import ValidationError

from app.calendar_tools import build_calendar_tools
from tests.test_calendar_service import FakeService


class CalendarToolTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.tools = {tool.name: tool for tool in build_calendar_tools(self.service)}

    def test_all_five_tools_are_registered(self):
        self.assertEqual(
            set(self.tools),
            {
                "create_calendar_event",
                "list_calendar_events",
                "search_calendar_events",
                "update_calendar_event",
                "delete_calendar_event",
            },
        )

    def test_create_tool_parses_iso_and_defaults_to_one_hour(self):
        result = self.tools["create_calendar_event"].invoke(
            {"title": "ML study", "start_time": "2026-08-25T18:00:00+05:30"}
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["title"], "ML study")
        call = self.service.events().calls[0]
        self.assertEqual(call[0], "insert")
        self.assertEqual(
            call[1]["body"]["end"]["dateTime"], "2026-08-25T19:00:00+05:30"
        )

    def test_search_tool_calls_google_query(self):
        result = self.tools["search_calendar_events"].invoke({"query": "DSA"})

        self.assertTrue(result["success"])
        self.assertEqual(self.service.events().calls[0][1]["q"], "DSA")

    def test_tool_schemas_are_pydantic_contracts(self):
        schema = self.tools["create_calendar_event"].get_input_schema().model_json_schema()
        self.assertIn("title", schema["properties"])
        self.assertIn("start_time", schema["properties"])

    def test_create_tool_rejects_an_end_before_the_start(self):
        with self.assertRaises(ValidationError):
            self.tools["create_calendar_event"].invoke(
                {
                    "title": "DSA",
                    "start_time": "2026-09-11T18:00:00+05:30",
                    "end_time": "2026-09-11T17:00:00+05:30",
                }
            )
        self.assertNotIn("insert", [name for name, _ in self.service.events().calls])

    def test_update_tool_rejects_only_one_side_of_a_time_range(self):
        with self.assertRaises(ValidationError):
            self.tools["update_calendar_event"].invoke(
                {
                    "event_id": "event-1",
                    "start_time": "2026-09-11T18:00:00+05:30",
                }
            )
        self.assertNotIn("patch", [name for name, _ in self.service.events().calls])

    def test_list_tool_rejects_an_invalid_result_limit(self):
        with self.assertRaises(ValidationError):
            self.tools["list_calendar_events"].invoke({"max_results": 0})
        self.assertNotIn("list", [name for name, _ in self.service.events().calls])
