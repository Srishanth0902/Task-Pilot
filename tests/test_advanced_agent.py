import unittest

from langchain_core.messages import AIMessage

from app.graph_agent import CalendarConversation, QueryPlan, _coerce_query_plan
from tests.test_calendar_service import FakeService


class Planner:
    def __init__(self, *plans):
        self.plans = list(plans)

    def invoke(self, messages):
        return self.plans.pop(0)


def event(event_id, title, start, end):
    return {
        "id": event_id,
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "status": "confirmed",
    }


class AdvancedAgentTests(unittest.TestCase):
    def test_provider_specific_intent_tool_call_is_normalised(self):
        plan = _coerce_query_plan(
            {
                "raw": AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "free_slot",
                            "args": {
                                "title": "DSA practice",
                                "duration_minutes": 120,
                                "time_min": "2026-09-11T00:00:00+05:30",
                                "time_max": "2026-09-12T00:00:00+05:30",
                            },
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                "parsed": None,
                "parsing_error": ValueError("Unknown tool type"),
            }
        )

        self.assertEqual(plan.intent, "free_slot")
        self.assertEqual(plan.duration_minutes, 120)

    def test_bulk_shift_shows_plan_and_requires_yes_before_updates(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "study-1",
                        "DSA study",
                        "2026-09-10T18:00:00+05:30",
                        "2026-09-10T19:00:00+05:30",
                    ),
                    event(
                        "study-2",
                        "ML study",
                        "2026-09-10T20:00:00+05:30",
                        "2026-09-10T21:00:00+05:30",
                    ),
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(
                QueryPlan(
                    intent="bulk_update",
                    search_query="study",
                    time_min="2026-09-10T00:00:00+05:30",
                    time_max="2026-09-11T00:00:00+05:30",
                    shift_minutes=1440,
                )
            ),
        )

        proposal = conversation.ask(
            "Move all my study tasks from today to tomorrow", thread_id="bulk-move"
        )

        self.assertTrue(proposal["awaiting_confirmation"])
        self.assertEqual(len(proposal["proposed_changes"]), 2)
        self.assertIn("Proceed? (yes/no)", proposal["response"])
        self.assertNotIn("patch", [name for name, _ in service.events().calls])

        result = conversation.ask("yes", thread_id="bulk-move")

        patches = [call for call in service.events().calls if call[0] == "patch"]
        self.assertEqual(len(patches), 2)
        self.assertEqual(
            patches[0][1]["body"]["start"]["dateTime"],
            "2026-09-11T18:00:00+05:30",
        )
        self.assertEqual(result["response"], "Successfully updated 2 events.")
        self.assertTrue(result["verified"])

    def test_bulk_delete_cancel_never_calls_delete(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "cancelled-1",
                        "Cancelled meeting",
                        "2026-09-11T10:00:00+05:30",
                        "2026-09-11T11:00:00+05:30",
                    )
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(
                QueryPlan(
                    intent="bulk_delete",
                    search_query="cancelled",
                    time_min="2026-09-07T00:00:00+05:30",
                    time_max="2026-09-14T00:00:00+05:30",
                )
            ),
        )
        conversation.ask("Delete all cancelled events this week", thread_id="bulk-delete")

        result = conversation.ask("no", thread_id="bulk-delete")

        self.assertNotIn("delete", [name for name, _ in service.events().calls])
        self.assertEqual(result["response"], "Cancelled. No calendar events were changed.")
        self.assertFalse(result["awaiting_confirmation"])

    def test_conflict_blocks_create_and_followup_alternative_succeeds(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "meeting-1",
                        "Project Meeting",
                        "2026-09-11T15:00:00+05:30",
                        "2026-09-11T16:00:00+05:30",
                    )
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(
                QueryPlan(
                    intent="create",
                    title="ML revision",
                    start_time="2026-09-11T15:00:00+05:30",
                    end_time="2026-09-11T16:00:00+05:30",
                ),
                QueryPlan(intent="unknown", continue_previous=True),
            ),
        )

        conflict = conversation.ask(
            "Schedule ML revision tomorrow at 3 PM", thread_id="conflict"
        )

        self.assertIn("conflicts with Project Meeting", conflict["response"])
        self.assertTrue(conflict["alternatives"])
        self.assertNotIn("insert", [name for name, _ in service.events().calls])

        created = conversation.ask("4 PM", thread_id="conflict")

        insert = [call for call in service.events().calls if call[0] == "insert"][-1]
        self.assertEqual(
            insert[1]["body"]["start"]["dateTime"],
            "2026-09-11T16:00:00+05:30",
        )
        self.assertTrue(created["verified"])

    def test_free_slot_is_calculated_then_confirmed_before_create(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "morning",
                        "Morning meeting",
                        "2026-09-11T09:00:00+05:30",
                        "2026-09-11T10:00:00+05:30",
                    ),
                    event(
                        "lunch",
                        "Lunch",
                        "2026-09-11T13:00:00+05:30",
                        "2026-09-11T14:00:00+05:30",
                    ),
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(
                QueryPlan(
                    intent="free_slot",
                    title="DSA practice",
                    duration_minutes=120,
                    time_min="2026-09-11T00:00:00+05:30",
                    time_max="2026-09-12T00:00:00+05:30",
                )
            ),
        )

        proposal = conversation.ask(
            "Find a 2-hour free slot tomorrow and schedule DSA practice",
            thread_id="free-slot",
        )

        self.assertTrue(proposal["awaiting_confirmation"])
        self.assertEqual(
            proposal["proposed_changes"][0]["new_start"],
            "2026-09-11T10:00:00+05:30",
        )
        self.assertNotIn("insert", [name for name, _ in service.events().calls])

        created = conversation.ask("yes", thread_id="free-slot")

        insert = [call for call in service.events().calls if call[0] == "insert"][-1]
        self.assertEqual(
            insert[1]["body"]["start"]["dateTime"],
            "2026-09-11T10:00:00+05:30",
        )
        self.assertTrue(created["verified"])


if __name__ == "__main__":
    unittest.main()
