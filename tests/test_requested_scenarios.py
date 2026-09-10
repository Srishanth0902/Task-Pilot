"""Regression coverage for the requested natural-language scenarios."""

import unittest

from googleapiclient.errors import HttpError

from app.graph_agent import CalendarConversation, QueryPlan
from tests.test_calendar_service import FakeResponse, FakeService


class Planner:
    def __init__(self, *plans):
        self.plans = list(plans)

    def invoke(self, messages):
        value = self.plans.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def event(event_id: str, title: str, start: str, end: str) -> dict:
    return {
        "id": event_id,
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "status": "confirmed",
    }


class RequestedPromptScenarios(unittest.TestCase):
    def test_normal_add_dsa_tomorrow_at_6_uses_ist(self):
        service = FakeService()
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(
                QueryPlan(
                    intent="create",
                    title="DSA",
                    start_time="2026-09-11T06:00:00Z",
                    end_time="2026-09-11T07:00:00Z",
                )
            ),
        )

        result = conversation.ask("Add DSA tomorrow at 6", thread_id="normal-add")

        insert = [call for call in service.events().calls if call[0] == "insert"][-1]
        self.assertEqual(
            insert[1]["body"]["start"]["dateTime"],
            "2026-09-11T06:00:00+05:30",
        )
        self.assertEqual(insert[1]["body"]["start"]["timeZone"], "Asia/Kolkata")
        self.assertTrue(result["verified"])
        self.assertIn("6:00 AM–7:00 AM IST", result["response"])

    def test_ambiguous_delete_my_meeting_asks_before_deleting(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "meeting-1", "Project meeting",
                        "2026-09-11T10:00:00+05:30",
                        "2026-09-11T11:00:00+05:30",
                    ),
                    event(
                        "meeting-2", "Team meeting",
                        "2026-09-12T15:00:00+05:30",
                        "2026-09-12T16:00:00+05:30",
                    ),
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(QueryPlan(intent="delete", search_query="meeting")),
        )

        result = conversation.ask("Delete my meeting", thread_id="ambiguous-delete")

        self.assertIn("multiple matching events", result["response"])
        self.assertIn("Project meeting", result["response"])
        self.assertIn("Team meeting", result["response"])
        self.assertNotIn("delete", [name for name, _ in service.events().calls])

    def test_multiple_move_my_study_task_asks_which_one(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "study-1", "DSA study task",
                        "2026-09-11T18:00:00+05:30",
                        "2026-09-11T19:00:00+05:30",
                    ),
                    event(
                        "study-2", "ML study task",
                        "2026-09-12T18:00:00+05:30",
                        "2026-09-12T19:00:00+05:30",
                    ),
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(QueryPlan(intent="update", search_query="study task")),
        )

        result = conversation.ask("Move my study task", thread_id="multiple-move")

        self.assertIn("multiple matching events", result["response"])
        self.assertNotIn("patch", [name for name, _ in service.events().calls])

    def test_conflict_schedule_at_occupied_time_does_not_create(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "occupied", "Existing meeting",
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
                    title="task",
                    start_time="2026-09-11T15:00:00+05:30",
                    end_time="2026-09-11T16:00:00+05:30",
                )
            ),
        )

        result = conversation.ask(
            "Schedule a task at an occupied time", thread_id="occupied-time"
        )

        self.assertIn("conflicts with Existing meeting", result["response"])
        self.assertTrue(result["alternatives"])
        self.assertNotIn("insert", [name for name, _ in service.events().calls])

    def test_bulk_move_all_study_sessions_by_one_hour(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "study-1", "DSA study",
                        "2026-09-11T10:00:00+05:30",
                        "2026-09-11T11:00:00+05:30",
                    ),
                    event(
                        "study-2", "ML study",
                        "2026-09-11T12:00:00+05:30",
                        "2026-09-11T13:00:00+05:30",
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
                    shift_minutes=60,
                )
            ),
        )

        proposal = conversation.ask(
            "Move all study sessions by 1 hour", thread_id="bulk-one-hour"
        )
        self.assertTrue(proposal["awaiting_confirmation"])
        self.assertEqual(len(proposal["proposed_changes"]), 2)
        self.assertNotIn("patch", [name for name, _ in service.events().calls])

        completed = conversation.ask("yes", thread_id="bulk-one-hour")
        patches = [call for call in service.events().calls if call[0] == "patch"]
        self.assertEqual(len(patches), 2)
        self.assertEqual(
            patches[0][1]["body"]["start"]["dateTime"],
            "2026-09-11T11:00:00+05:30",
        )
        self.assertEqual(completed["response"], "Successfully updated 2 events.")


class RequestedErrorScenarios(unittest.TestCase):
    def test_llm_planner_error_is_returned_as_a_safe_agent_error(self):
        conversation = CalendarConversation(
            None,
            FakeService(),
            planner=Planner(RuntimeError("provider unavailable")),
        )

        result = conversation.ask("Add DSA tomorrow at 6", thread_id="llm-error")

        self.assertFalse(result["verified"])
        self.assertIn("Could not understand the request", result["response"])
        self.assertIn("provider unavailable", result["response"])

    def test_calendar_search_error_does_not_delete_or_crash(self):
        denied = HttpError(FakeResponse(403), b"forbidden")
        service = FakeService(list_error=denied)
        conversation = CalendarConversation(
            None,
            service,
            planner=Planner(QueryPlan(intent="delete", search_query="meeting")),
        )

        result = conversation.ask("Delete my meeting", thread_id="calendar-error")

        self.assertIn("Calendar search failed", result["response"])
        self.assertNotIn("delete", [name for name, _ in service.events().calls])


if __name__ == "__main__":
    unittest.main()
