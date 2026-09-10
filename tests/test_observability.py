import unittest
from unittest.mock import patch

from app.config import PROJECT_ROOT
from app.graph_agent import CalendarConversation, QueryPlan
from app.observability import REDACTED, safe_error_detail, sanitize_log_data
from tests.test_calendar_service import FakeService


class Planner:
    def invoke(self, messages):
        return QueryPlan(intent="list")


class ObservabilityTests(unittest.TestCase):
    def test_secret_values_and_sensitive_keys_are_redacted(self):
        openrouter_secret = "sk-" + "or-v1-abc123"
        cleaned = sanitize_log_data(
            {
                "api_key": "secret",
                "nested": {
                    "authorization": "Bearer secret-token",
                    "text": f"key {openrouter_secret} should disappear",
                },
            }
        )

        self.assertEqual(cleaned["api_key"], REDACTED)
        self.assertEqual(cleaned["nested"]["authorization"], REDACTED)
        self.assertNotIn(openrouter_secret, cleaned["nested"]["text"])
        private_secret = "sk-" + "or-v1-private"
        self.assertNotIn(
            private_secret,
            safe_error_detail(RuntimeError(f"failed with {private_secret}")),
        )

    @patch("app.graph_agent.log_workflow")
    def test_agent_logs_query_nodes_tool_io_and_final_response(self, logger):
        conversation = CalendarConversation(None, FakeService(), planner=Planner())

        conversation.ask("List events", thread_id="logging-test")

        event_names = [call.args[0] for call in logger.call_args_list]
        self.assertIn("user_query", event_names)
        self.assertIn("graph_node_started", event_names)
        self.assertIn("tool_input", event_names)
        self.assertIn("tool_output", event_names)
        self.assertIn("final_response", event_names)

    def test_sensitive_runtime_files_and_logs_are_git_ignored(self):
        ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in (".env", "credentials.json", "token.json", "logs/"):
            self.assertIn(entry, ignore)


if __name__ == "__main__":
    unittest.main()
