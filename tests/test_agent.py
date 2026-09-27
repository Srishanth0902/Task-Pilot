import unittest

from app.agent import create_calendar_agent, run_calendar_request
from app.graph_agent import (
    CalendarConversation,
    QueryPlan,
    _clock_from_text,
    _duration_minutes_from_text,
)
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
                "load_calendar_window",
                "find_free_slot",
                "plan_bulk_operation",
                "detect_conflicts",
                "request_confirmation",
                "execute_bulk_action",
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
            "Add ML study tomorrow at 6 PM for 1 hour",
            model=None,
            service=service,
            planner=planner,
        )

        self.assertEqual(
            [call[0] for call in service.events().calls], ["list", "insert"]
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["intent"], "create")
        self.assertEqual(result["user_query"], "Add ML study tomorrow at 6 PM for 1 hour")
        self.assertIsNone(result["pending_action"])
        self.assertIn("tool_result", result)
        self.assertIn("selected_event", result)
        self.assertEqual(len(result["messages"]), 2)

    def test_create_without_duration_asks_then_uses_followup_exactly(self):
        service = FakeService()
        conversation = CalendarConversation(
            None,
            service,
            planner=SequentialPlanner(
                QueryPlan(
                    intent="create",
                    title="yoga",
                    start_time="2026-09-14T17:00:00+05:30",
                    end_time="2026-09-14T18:00:00+05:30",
                ),
                QueryPlan(intent="unknown"),
            ),
        )

        first = conversation.ask(
            "Can you make a slot for yoga at 5 PM today?",
            thread_id="create-duration",
        )

        self.assertEqual(
            first["response"],
            "How long should yoga last? For example, 30 minutes or 1 hour.",
        )
        self.assertTrue(first["pending_action"]["awaiting_duration"])
        self.assertNotIn("end_time", first["pending_action"])
        self.assertFalse(service.events().calls)

        second = conversation.ask("45 minutes", thread_id="create-duration")

        insert = [call for call in service.events().calls if call[0] == "insert"][-1]
        self.assertEqual(insert[1]["body"]["start"]["dateTime"], "2026-09-14T17:00:00+05:30")
        self.assertEqual(insert[1]["body"]["end"]["dateTime"], "2026-09-14T17:45:00+05:30")
        self.assertTrue(second["verified"])

    def test_fractional_duration_followup_preserves_saved_7_pm_start(self):
        service = FakeService()
        planner = SequentialPlanner(
            QueryPlan(
                intent="create",
                title="dsa",
                start_time="2026-09-15T19:00:00+05:30",
                # Simulate the model's former unauthorized one-hour default.
                end_time="2026-09-15T20:00:00+05:30",
            ),
            # This bad fallback must never be invoked for a duration-only reply.
            QueryPlan(
                intent="create",
                start_time="2026-09-15T01:00:00+05:30",
                continue_previous=True,
            ),
        )
        conversation = CalendarConversation(None, service, planner=planner)

        first = conversation.ask("dsa on 7pm today", thread_id="fractional-duration")
        second = conversation.ask("1 and half hour", thread_id="fractional-duration")

        self.assertIn("How long should dsa last?", first["response"])
        self.assertEqual(len(planner.calls), 1)
        insert = [call for call in service.events().calls if call[0] == "insert"][-1]
        self.assertEqual(insert[1]["body"]["start"]["dateTime"], "2026-09-15T19:00:00+05:30")
        self.assertEqual(insert[1]["body"]["end"]["dateTime"], "2026-09-15T20:30:00+05:30")
        self.assertTrue(second["verified"])

    def test_natural_fractional_duration_phrases_are_not_clock_times(self):
        cases = {
            "1 and half hour": 90,
            "1 and a half hours": 90,
            "one and a half hours": 90,
            "an hour and a half": 90,
            "1.5 hours": 90,
            "1 hour and 30 minutes": 90,
        }

        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(_duration_minutes_from_text(phrase), expected)
                self.assertIsNone(_clock_from_text(phrase))

    def test_explicit_create_range_does_not_need_duration_followup(self):
        service = FakeService()
        result = CalendarConversation(
            None,
            service,
            planner=SequentialPlanner(
                QueryPlan(
                    intent="create",
                    title="Interview prep",
                    start_time="2026-09-14T17:00:00+05:30",
                    end_time="2026-09-14T18:00:00+05:30",
                )
            ),
        ).ask("Add interview prep today from 5 PM until 5:30 PM")

        insert = [call for call in service.events().calls if call[0] == "insert"][-1]
        self.assertEqual(insert[1]["body"]["end"]["dateTime"], "2026-09-14T17:30:00+05:30")
        self.assertTrue(result["verified"])

    def test_relative_start_is_not_mistaken_for_duration(self):
        service = FakeService()
        result = CalendarConversation(
            None,
            service,
            planner=SequentialPlanner(
                QueryPlan(
                    intent="create",
                    title="Yoga",
                    start_time="2026-09-14T19:00:00+05:30",
                    end_time="2026-09-14T20:00:00+05:30",
                )
            ),
        ).ask("Schedule yoga in two hours")

        self.assertIn("How long should Yoga last?", result["response"])
        self.assertFalse(service.events().calls)

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

        conversation = CalendarConversation(None, service, planner=planner)
        proposal = conversation.ask(
            "Delete my meeting tomorrow", thread_id="confirm-single-delete"
        )

        calls = service.events().calls
        self.assertEqual([call[0] for call in calls], ["list"])
        self.assertTrue(proposal["awaiting_confirmation"])
        self.assertIn("Delete Project meeting", proposal["response"])

        result = conversation.ask("yes", thread_id="confirm-single-delete")

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

        proposal = conversation.ask("The one at 6 PM", thread_id="choose-one")

        self.assertTrue(proposal["awaiting_confirmation"])
        self.assertIn("Delete Team meeting", proposal["response"])
        self.assertNotIn("delete", [call[0] for call in service.events().calls])

        result = conversation.ask("yes", thread_id="choose-one")

        delete_call = service.events().calls[-1]
        self.assertEqual(delete_call[0], "delete")
        self.assertEqual(delete_call[1]["eventId"], "meeting-2")
        self.assertEqual(result["response"], "Deleted Team meeting.")
        self.assertEqual(len(result["messages"]), 6)

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
