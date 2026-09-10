import unittest

from app.agent import create_calendar_agent, run_calendar_request
from app.graph_agent import CalendarConversation, QueryPlan
from tests.test_calendar_service import FakeService


class SequentialPlanner:
    """Deterministic structured planner used to exercise graph routing offline."""

    def __init__(self, *plans):
        self.plans = list(plans)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        value = self.plans.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def event(event_id, title, start, end):
    return {
        "id": event_id,
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "status": "confirmed",
    }


class LangGraphAgentTests(unittest.TestCase):
    def test_graph_contains_the_week3_workflow_nodes(self):
        graph = create_calendar_agent(
            None,
            FakeService(),
            planner=SequentialPlanner(QueryPlan(intent="list")),
        )

        self.assertTrue(
            {
                "understand_query",
                "search_calendar",
                "resolve_event",
                "execute_action",
                "verify_result",
                "generate_response",
                "handle_error",
            }.issubset(graph.get_graph().nodes)
        )

    def test_create_uses_multi_node_graph_and_tracks_week3_state(self):
        service = FakeService()
        planner = SequentialPlanner(
            QueryPlan(
                intent="create",
                title="ML study",
                start_time="2026-09-11T18:00:00+05:30",
            )
        )

        result = run_calendar_request(
            "Add ML study tomorrow at 6 PM",
            model=None,
            service=service,
            planner=planner,
        )

        self.assertEqual(service.events().calls[0][0], "insert")
        self.assertTrue(result["verified"])
        self.assertEqual(result["intent"], "create")
        self.assertEqual(result["user_query"], "Add ML study tomorrow at 6 PM")
        self.assertIsNone(result["pending_action"])
        self.assertIn("tool_result", result)
        self.assertIn("selected_event", result)
        self.assertEqual(len(result["messages"]), 2)

    def test_delete_searches_and_resolves_a_single_event_before_mutating(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "meeting-1",
                        "Project meeting",
                        "2026-09-11T10:00:00+05:30",
                        "2026-09-11T11:00:00+05:30",
                    )
                ]
            }
        )
        planner = SequentialPlanner(
            QueryPlan(intent="delete", search_query="meeting")
        )

        result = run_calendar_request(
            "Delete my meeting tomorrow",
            model=None,
            service=service,
            planner=planner,
        )

        calls = service.events().calls
        self.assertEqual([call[0] for call in calls], ["list", "delete"])
        self.assertEqual(calls[1][1]["eventId"], "meeting-1")
        self.assertEqual(result["response"], "Deleted Project meeting.")

    def test_multiple_matches_ask_for_clarification_without_deleting(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "meeting-1",
                        "Project meeting",
                        "2026-09-11T10:00:00+05:30",
                        "2026-09-11T11:00:00+05:30",
                    ),
                    event(
                        "meeting-2",
                        "Team meeting",
                        "2026-09-11T18:00:00+05:30",
                        "2026-09-11T19:00:00+05:30",
                    ),
                ]
            }
        )
        conversation = CalendarConversation(
            None,
            service,
            planner=SequentialPlanner(QueryPlan(intent="delete", search_query="meeting")),
        )

        result = conversation.ask("Delete my meeting tomorrow", thread_id="ambiguous")

        self.assertEqual([call[0] for call in service.events().calls], ["list"])
        self.assertIn("multiple matching events", result["response"])
        self.assertEqual(len(result["candidate_events"]), 2)
        self.assertEqual(result["pending_action"]["intent"], "delete")

    def test_no_match_returns_safely_without_mutating(self):
        service = FakeService(list_result={"items": []})
        conversation = CalendarConversation(
            None,
            service,
            planner=SequentialPlanner(QueryPlan(intent="delete", search_query="missing")),
        )

        result = conversation.ask("Delete my missing event", thread_id="no-match")

        self.assertEqual([call[0] for call in service.events().calls], ["list"])
        self.assertEqual(
            result["response"], "I could not find a matching calendar event."
        )

    def test_followup_selects_one_ambiguous_event_from_saved_context(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "meeting-1",
                        "Project meeting",
                        "2026-09-11T10:00:00+05:30",
                        "2026-09-11T11:00:00+05:30",
                    ),
                    event(
                        "meeting-2",
                        "Team meeting",
                        "2026-09-11T18:00:00+05:30",
                        "2026-09-11T19:00:00+05:30",
                    ),
                ]
            }
        )
        planner = SequentialPlanner(
            QueryPlan(intent="delete", search_query="meeting"),
            QueryPlan(intent="unknown", continue_previous=True),
        )
        conversation = CalendarConversation(None, service, planner=planner)
        conversation.ask("Delete my meeting tomorrow", thread_id="choose-one")

        result = conversation.ask("The one at 6 PM", thread_id="choose-one")

        delete_call = service.events().calls[-1]
        self.assertEqual(delete_call[0], "delete")
        self.assertEqual(delete_call[1]["eventId"], "meeting-2")
        self.assertEqual(result["response"], "Deleted Team meeting.")
        self.assertEqual(len(result["messages"]), 4)

    def test_followup_time_completes_a_pending_move_and_preserves_duration(self):
        service = FakeService(
            list_result={
                "items": [
                    event(
                        "dsa-1",
                        "DSA session",
                        "2026-09-11T18:00:00+05:30",
                        "2026-09-11T19:00:00+05:30",
                    )
                ]
            }
        )
        planner = SequentialPlanner(
            QueryPlan(intent="update", search_query="DSA session"),
            QueryPlan(intent="unknown", continue_previous=True),
        )
        conversation = CalendarConversation(None, service, planner=planner)

        first = conversation.ask(
            "Move my DSA session tomorrow", thread_id="move-dsa"
        )
        self.assertIn("What time should I move it to?", first["response"])
        self.assertEqual([call[0] for call in service.events().calls], ["list"])

        second = conversation.ask("8 PM", thread_id="move-dsa")

        patch = service.events().calls[-1]
        self.assertEqual(patch[0], "patch")
        self.assertEqual(patch[1]["eventId"], "dsa-1")
        self.assertEqual(
            patch[1]["body"]["start"]["dateTime"],
            "2026-09-11T20:00:00+05:30",
        )
        self.assertEqual(
            patch[1]["body"]["end"]["dateTime"],
            "2026-09-11T21:00:00+05:30",
        )
        self.assertTrue(second["verified"])

    def test_planner_failure_routes_to_error_handler(self):
        result = run_calendar_request(
            "Do something",
            model=None,
            service=FakeService(),
            planner=SequentialPlanner(RuntimeError("provider unavailable")),
        )

        self.assertIn("provider unavailable", result["response"])
        self.assertFalse(result.get("verified", False))

    def test_agent_requires_model_or_test_planner(self):
        with self.assertRaisesRegex(ValueError, "chat model"):
            create_calendar_agent(None, FakeService())


if __name__ == "__main__":
    unittest.main()
