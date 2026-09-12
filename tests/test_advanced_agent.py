import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
    @patch("app.graph_agent.local_now")
    def test_availability_question_infers_tomorrow_after_6_without_model_times(
        self, mocked_now
    ):
        mocked_now.return_value = datetime(
            2026, 9, 11, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "busy-1",
                        "Dinner",
                        "2026-09-12T18:00:00+05:30",
                        "2026-09-12T19:00:00+05:30",
                    )
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(QueryPlan(intent="free_slot")),
        )

        result = conversation.ask(
            "I have to study for my interview, so can you give me the slots "
            "that are available tomorrow after 6 pm?",
            thread_id="availability-exact",
        )

        list_call = [call for call in service.events().calls if call[0] == "list"][-1]
        self.assertEqual(list_call[1]["timeMin"], "2026-09-12T18:00:00+05:30")
        self.assertEqual(list_call[1]["timeMax"], "2026-09-12T21:00:00+05:30")
        self.assertEqual(result["intent"], "free_slot")
        self.assertTrue(result["verified"])
        self.assertFalse(result["awaiting_confirmation"])
        self.assertIn("Saturday, 12 September 2026, 7:00 PM–8:00 PM IST", result["response"])
        self.assertNotIn("insert", [name for name, _ in service.events().calls])

    @patch("app.graph_agent.local_now")
    def test_free_slot_range_followup_does_not_depend_on_model_continue_flag(
        self, mocked_now
    ):
        mocked_now.return_value = datetime(
            2026, 9, 11, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        service = FakeService()
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(
                QueryPlan(intent="free_slot"),
                QueryPlan(intent="free_slot", continue_previous=False),
            ),
        )

        first = conversation.ask("Can you find free slots?", thread_id="range-followup")
        second = conversation.ask(
            "Tomorrow and after 6 PM", thread_id="range-followup"
        )

        self.assertIn("Which day", first["response"])
        self.assertIn("Saturday, 12 September 2026, 6:00 PM–7:00 PM IST", second["response"])
        self.assertFalse(second["awaiting_confirmation"])

    def test_explicit_user_clock_overrides_model_utc_clock(self):
        service = FakeService()
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(
                QueryPlan(
                    intent="create",
                    title="yoga",
                    start_time="2026-09-11T07:00:00Z",
                    end_time="2026-09-11T08:00:00Z",
                )
            ),
        )

        result = conversation.ask(
            "Tomorrow I need to do yoga at 7 AM for 1 hour",
            thread_id="explicit-ist",
        )

        insert = [call for call in service.events().calls if call[0] == "insert"][-1]
        self.assertEqual(
            insert[1]["body"]["start"]["dateTime"],
            "2026-09-11T07:00:00+05:30",
        )
        self.assertEqual(
            result["response"],
            "Created yoga — Friday, 11 September 2026, 7:00 AM–8:00 AM IST.",
        )

    def test_delete_all_tasks_lists_every_event_before_confirmation(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "yoga-1",
                        "yoga",
                        "2026-09-11T07:00:00+05:30",
                        "2026-09-11T08:00:00+05:30",
                    )
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(QueryPlan(intent="bulk_delete", search_query="tasks")),
        )

        proposal = conversation.ask("Remove all the tasks", thread_id="delete-all")

        list_calls = [call for call in service.events().calls if call[0] == "list"]
        self.assertEqual(len(list_calls), 1)
        self.assertNotIn("q", list_calls[0][1])
        self.assertTrue(proposal["awaiting_confirmation"])
        self.assertEqual(len(proposal["affected_events"]), 1)
        self.assertIn("Delete yoga", proposal["response"])
        self.assertNotIn("delete", [name for name, _ in service.events().calls])

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
