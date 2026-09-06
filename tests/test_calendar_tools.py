import unittest

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
