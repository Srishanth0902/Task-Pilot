"""Live Qwen interpretation checks with a fake calendar; no Google writes.

Run with a separate LOG_FILE when the backend is running on Windows.
"""
from datetime import datetime
from unittest.mock import patch

from app.graph_agent import CalendarConversation
from app.llm import create_openrouter_model
from tests.test_rescheduling import CoordinatedReschedulingTests


def main():
    cases = [
        ("explicit", "Schedule an urgent meeting tomorrow at 6 PM for one hour and move Yoga to the next available slot", False),
        ("review", "Schedule an urgent meeting tomorrow at 6 PM for one hour", True),
    ]
    with patch("app.graph_agent.local_now", return_value=datetime.fromisoformat("2026-09-12T09:00:00+05:30")):
        for name, prompt, review in cases:
            service = CoordinatedReschedulingTests().service()
            result = CalendarConversation(create_openrouter_model(), service).ask(prompt)
            assert bool(result.get("awaiting_confirmation")) == review, name
            assert (len(result.get("proposed_changes", [])) == 2 if review else result.get("verified")), name
            writes = [n for n, _ in service.events().calls if n in {"patch", "insert", "delete"}]
            assert writes == ([] if review else ["patch", "insert"]), name
            print(f"{name}: PASS")
    print("2/2 live model checks passed; synthetic calendar only.")


if __name__ == "__main__":
    main()
