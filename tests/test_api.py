import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import AgentRuntime, create_app
from tests.test_calendar_service import FakeService


class FakeRuntime:
    def __init__(self):
        self.chat_calls = []
        self.event_calls = []
        self.raise_chat = False

    def health(self):
        return {
            "status": "ok",
            "service": "task-pilot",
            "model": "qwen/qwen3-30b-a3b",
            "timezone": "Asia/Kolkata",
            "openrouter_configured": True,
            "google_credentials_configured": True,
            "google_token_configured": True,
        }

    def chat(self, message, thread_id):
        if self.raise_chat:
            raise RuntimeError("provider unavailable")
        self.chat_calls.append((message, thread_id))
        return {
            "response": "Proposed changes. Proceed?",
            "intent": "bulk_update",
            "awaiting_confirmation": True,
            "confirmation_status": "pending",
            "affected_events": [{"event_id": "one", "title": "DSA"}],
            "proposed_changes": [{"action": "update", "event_id": "one"}],
            "conflict_events": [],
            "alternatives": [],
            "tool_result": {"success": True, "events": [{"title": "DSA"}]},
            "error": None,
        }

    def events(self, *, max_results, time_min, time_max):
        self.event_calls.append((max_results, time_min, time_max))
        return {
            "success": True,
            "count": 1,
            "events": [{"event_id": "one", "title": "DSA"}],
        }


class FastAPIBackendTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.client = TestClient(create_app(self.runtime))

    def test_health_is_available_without_initializing_external_services(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(response.json()["openrouter_configured"])
        self.assertEqual(self.runtime.chat_calls, [])

    def test_chat_preserves_supplied_thread_and_returns_ui_metadata(self):
        response = self.client.post(
            "/chat",
            json={"message": "Move all study events tomorrow", "thread_id": "thread-1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["thread_id"], "thread-1")
        self.assertTrue(payload["requires_confirmation"])
        self.assertEqual(payload["intent"], "bulk_update")
        self.assertEqual(len(payload["proposed_changes"]), 1)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(self.runtime.chat_calls, [("Move all study events tomorrow", "thread-1")])

    def test_chat_generates_a_thread_id_when_omitted(self):
        response = self.client.post("/chat", json={"message": "List events"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["thread_id"])
        self.assertEqual(response.json()["thread_id"], self.runtime.chat_calls[0][1])

    def test_blank_chat_message_is_rejected(self):
        response = self.client.post("/chat", json={"message": "   "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.runtime.chat_calls, [])

    def test_events_returns_structured_calendar_data(self):
        response = self.client.get(
            "/events",
            params={"max_results": 5, "time_min": "tomorrow", "time_max": "next week"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(
            self.runtime.event_calls, [(5, "tomorrow", "next week")]
        )

    def test_invalid_event_limit_is_rejected_before_runtime(self):
        response = self.client.get("/events", params={"max_results": 0})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.runtime.event_calls, [])

    def test_runtime_failure_becomes_service_unavailable(self):
        self.runtime.raise_chat = True

        response = self.client.post("/chat", json={"message": "List events"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("provider unavailable", response.json()["detail"])

    def test_openapi_contains_all_week5_routes(self):
        paths = self.client.get("/openapi.json").json()["paths"]

        self.assertTrue({"/chat", "/events", "/health"}.issubset(paths))


class RuntimeIsolationTests(unittest.TestCase):
    @patch("app.api.create_openrouter_model")
    @patch("app.api.get_calendar_service")
    def test_events_does_not_initialize_the_llm(self, calendar_service, model):
        calendar_service.return_value = FakeService(list_result={"items": []})
        runtime = AgentRuntime()

        result = runtime.events(max_results=5, time_min=None, time_max=None)

        self.assertTrue(result["success"])
        calendar_service.assert_called_once_with()
        model.assert_not_called()

    def test_reversed_event_range_is_rejected_before_google(self):
        runtime = AgentRuntime()

        with self.assertRaisesRegex(ValueError, "time_max must be after"):
            runtime.events(
                max_results=5,
                time_min="2026-09-12T10:00:00+05:30",
                time_max="2026-09-12T09:00:00+05:30",
            )


if __name__ == "__main__":
    unittest.main()
