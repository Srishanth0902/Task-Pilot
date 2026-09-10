"""Small typed HTTP client used by the Streamlit frontend."""

from __future__ import annotations

from typing import Any

import requests

from app.config import TASK_PILOT_API_URL


class TaskPilotAPIError(RuntimeError):
    pass


class TaskPilotAPI:
    def __init__(self, base_url: str = TASK_PILOT_API_URL, *, timeout: float = 90):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as error:
            raise TaskPilotAPIError(f"Cannot reach the Task Pilot backend: {error}") from error
        try:
            payload = response.json()
        except requests.JSONDecodeError as error:
            raise TaskPilotAPIError(
                f"Backend returned HTTP {response.status_code} with invalid JSON."
            ) from error
        if not response.ok:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            raise TaskPilotAPIError(f"Backend error {response.status_code}: {detail}")
        return payload

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def events(self, *, max_results: int = 10) -> dict[str, Any]:
        return self._request("GET", "/events", params={"max_results": max_results})

    def chat(self, message: str, thread_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/chat",
            json={"message": message, "thread_id": thread_id},
        )
