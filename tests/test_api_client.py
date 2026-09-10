import unittest
from unittest.mock import Mock, patch

import requests

from app.api_client import TaskPilotAPI, TaskPilotAPIError


class APIClientTests(unittest.TestCase):
    def setUp(self):
        self.client = TaskPilotAPI("http://example.test", timeout=3)

    @patch("app.api_client.requests.request")
    def test_chat_posts_message_and_thread(self, request):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"response": "Done", "thread_id": "thread-1"}
        request.return_value = response

        result = self.client.chat("List events", "thread-1")

        self.assertEqual(result["response"], "Done")
        request.assert_called_once_with(
            "POST",
            "http://example.test/chat",
            timeout=3,
            json={"message": "List events", "thread_id": "thread-1"},
        )

    @patch("app.api_client.requests.request")
    def test_http_error_raises_readable_client_error(self, request):
        response = Mock(ok=False, status_code=503)
        response.json.return_value = {"detail": "provider unavailable"}
        request.return_value = response

        with self.assertRaisesRegex(TaskPilotAPIError, "provider unavailable"):
            self.client.health()

    @patch("app.api_client.requests.request")
    def test_connection_error_is_wrapped(self, request):
        request.side_effect = requests.ConnectionError("offline")

        with self.assertRaisesRegex(TaskPilotAPIError, "Cannot reach"):
            self.client.events()


if __name__ == "__main__":
    unittest.main()
