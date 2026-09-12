"""Live model regression replay; Google Calendar is never contacted."""
from unittest.mock import patch

from app.graph_agent import CalendarConversation
from app.llm import create_openrouter_model
from evaluation.run_difficult_prompts import SyntheticCalendar
from tests.test_calendar_service import FakeRequest
from tests.test_conversation_repairs import NOW
from tests.test_advanced_agent import event


class Calendar(SyntheticCalendar):
    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        row = dict(kwargs["body"], id=f"created-{len(self.items)}")
        self.items.append(row)
        return FakeRequest(dict(row))


def main():
    with patch("app.graph_agent.local_now", return_value=NOW):
        service = Calendar()
        service.items = []
        chat = CalendarConversation(create_openrouter_model(), service)
        result = chat.ask("What are the next tasks I need to do right now?")
        assert result["intent"] == "list" and result["verified"], result["response"]
        print("Current task query: PASS", flush=True)
        chat.ask("I want you to make a slot for yoga after 1 hour. I need to do yoga, so make a slot for that.")
        result = chat.ask("Okay, now at 9:50, I need to do my homework, so make a slot for that today.")
        assert len(service.items) == 1, result["response"]
        assert service.items[0]["start"]["dateTime"] == "2026-09-12T21:50:00+05:30"
        assert result.get("conflict_events"), result["response"]
        print("Completed PM context + conflict: PASS", flush=True)
        service.items.append(event("homework", "Homework", "2026-09-12T09:50:00+05:30", "2026-09-12T10:50:00+05:30"))
        result = chat.ask("Move my yoga task to 11 pm, and you can just keep the homework task at 9:50 pm.")
        assert result["awaiting_confirmation"] and len(result["proposed_changes"]) == 2, result["response"]
        result = chat.ask("yes")
        assert result["verified"], result["response"]
        starts = {e["summary"].lower(): e["start"]["dateTime"] for e in service.items}
        assert starts["homework"] == "2026-09-12T21:50:00+05:30", starts
        assert next(v for k,v in starts.items() if "yoga" in k) == "2026-09-12T23:00:00+05:30", starts
        print("Two-action correction + confirmation: PASS", flush=True)


if __name__ == "__main__":
    main()
