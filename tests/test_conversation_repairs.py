import unittest
from datetime import datetime
from unittest.mock import patch

from app.graph_agent import CalendarConversation, QueryPlan, EventMove
from tests.test_advanced_agent import Planner, event
from tests.test_calendar_service import FakeService


NOW = datetime.fromisoformat("2026-09-12T20:50:00+05:30")


class ConversationRepairTests(unittest.TestCase):
    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_current_task_phrasings_read_calendar(self, _):
        for query in ["What are the next tasks I need to do right now?", "What are the tasks that are there for now?", "What should I do next?"]:
            with self.subTest(query=query):
                service = FakeService()
                state = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="unknown"))).ask(query)
                self.assertTrue(state["verified"])
                self.assertEqual(service.events().calls[0][1]["timeMin"], NOW.isoformat())

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_bare_time_preserves_previous_completed_evening_event(self, _):
        service = FakeService()
        chat = CalendarConversation(None, service, planner=Planner(
            QueryPlan(intent="create", title="Yoga", start_time="2026-09-12T21:50:00+05:30", end_time="2026-09-12T22:50:00+05:30"),
            QueryPlan(intent="create", title="Homework", start_time="2026-09-12T09:50:00+05:30", end_time="2026-09-12T10:50:00+05:30")))
        chat.ask("Create Yoga at 9:50 PM")
        result = chat.ask("Okay, now at 9:50, I need to do my homework, so make a slot for that today.")
        self.assertEqual(result["tool_result"]["start"], "2026-09-12T21:50:00+05:30")

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_bare_clock_does_not_undo_model_pm_resolution(self, _):
        service = FakeService()
        result = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="create", title="Homework", start_time="2026-09-12T21:50:00+05:30"))).ask("Add homework at 9:50 today")
        self.assertEqual(result["tool_result"]["start"], "2026-09-12T21:50:00+05:30")

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_ambiguous_past_time_asks_instead_of_creating(self, _):
        service = FakeService()
        result = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="create", title="Homework", start_time="2026-09-12T09:50:00+05:30"))).ask("Add homework at 9:50 today")
        self.assertIn("AM or PM", result["response"])
        self.assertFalse(service.events().calls)

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_exact_two_move_request_keeps_both_instructions(self, _):
        service = FakeService(list_result={"items": [event("yoga", "Yoga", "2026-09-12T21:50:00+05:30", "2026-09-12T22:50:00+05:30"), event("homework", "Homework", "2026-09-12T09:50:00+05:30", "2026-09-12T10:50:00+05:30")]})
        chat = CalendarConversation(None, service, planner=Planner(QueryPlan(intent="update", search_query="Yoga", start_time="2026-09-12T23:00:00+05:30", end_time="2026-09-13T00:00:00+05:30", moves=[EventMove(search_query="Yoga", start_time="2026-09-12T23:00:00+05:30")])))
        result = chat.ask("Move my yoga task to 11 pm, and you can just keep the homework task at 9:50 pm.")
        self.assertTrue(result["awaiting_confirmation"])
        self.assertEqual([c["event_id"] for c in result["proposed_changes"]], ["yoga", "homework"])
        self.assertFalse(any(n == "patch" for n, _ in service.events().calls))
        result = chat.ask("yes")
        self.assertTrue(result["verified"])
        writes = [args for n,args in service.events().calls if n == "patch"]
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0]["body"]["end"]["dateTime"], "2026-09-13T00:00:00+05:30")
        self.assertEqual(writes[1]["body"]["start"]["dateTime"], "2026-09-12T21:50:00+05:30")

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_multi_move_ambiguity_or_collision_changes_nothing(self, _):
        for overlap in [True, False]:
            with self.subTest(overlap=overlap):
                items = [event("a", "Yoga", "2026-09-12T21:50:00+05:30", "2026-09-12T22:50:00+05:30"), event("b", "Homework", "2026-09-12T09:50:00+05:30", "2026-09-12T10:50:00+05:30")]
                if not overlap: items.append(event("c", "Yoga", "2026-09-12T18:00:00+05:30", "2026-09-12T19:00:00+05:30"))
                service = FakeService(list_result={"items":items})
                plan = QueryPlan(intent="update", moves=[EventMove(search_query="Yoga", start_time="2026-09-12T23:00:00+05:30"), EventMove(search_query="Homework", start_time="2026-09-12T23:00:00+05:30")])
                result = CalendarConversation(None, service, planner=Planner(plan)).ask("Move the two events")
                self.assertFalse(result["awaiting_confirmation"])
                self.assertTrue(result["clarification"])
                self.assertFalse(any(n == "patch" for n, _ in service.events().calls))

    @patch("app.graph_agent.local_now", return_value=NOW)
    def test_move_and_create_are_reviewed_and_executed_together(self, _):
        service = FakeService(list_result={"items": [event("yoga", "Yoga", "2026-09-12T21:50:00+05:30", "2026-09-12T22:50:00+05:30")]})
        plan = QueryPlan(intent="update", moves=[EventMove(search_query="Yoga", start_time="2026-09-12T23:00:00+05:30"), EventMove(search_query="Homework", start_time="2026-09-12T21:50:00+05:30", create_if_missing=True)])
        chat = CalendarConversation(None, service, planner=Planner(plan))
        result = chat.ask("Move Yoga to 11 PM and schedule Homework at 9:50 PM")
        self.assertTrue(result["awaiting_confirmation"])
        self.assertEqual([c["action"] for c in result["proposed_changes"]], ["update", "create"])
        self.assertFalse(any(n in {"patch", "insert"} for n, _ in service.events().calls))
        result = chat.ask("yes")
        self.assertTrue(result["verified"])
        writes = [(n, args) for n, args in service.events().calls if n in {"patch", "insert"}]
        self.assertEqual([n for n, _ in writes], ["patch", "insert"])
        self.assertEqual(writes[1][1]["body"]["summary"], "Homework")

    def test_completed_messages_are_sent_to_planner(self):
        class RecordingPlanner(Planner):
            def invoke(self, messages):
                self.context = messages[-1].content
                return super().invoke(messages)
        planner = RecordingPlanner(QueryPlan(intent="create", title="Yoga", start_time="2026-09-12T21:50:00+05:30"), QueryPlan(intent="list"))
        chat = CalendarConversation(None, FakeService(), planner=planner)
        chat.ask("Create Yoga at 9:50 PM")
        chat.ask("What next?")
        self.assertIn("Created Yoga", planner.context)
