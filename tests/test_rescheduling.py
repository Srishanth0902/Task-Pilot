import unittest
from app.graph_agent import CalendarConversation, QueryPlan
from tests.test_advanced_agent import Planner, event
from tests.test_calendar_service import FakeService, FakeResponse
from googleapiclient.errors import HttpError


def plan(strategy="relocate", target="Yoga"):
    return QueryPlan(intent="create", title="Urgent meeting",
        start_time="2026-09-13T18:00:00+05:30", end_time="2026-09-13T19:00:00+05:30",
        conflict_strategy=strategy, displacement_query=target)


class CoordinatedReschedulingTests(unittest.TestCase):
    def test_explicit_replacement_day_preserves_urgent_meeting_date(self):
        service = self.service()
        action = plan()
        action.relocation_date = "2026-09-14"
        result = CalendarConversation(None, service, planner=Planner(action)).ask(
            "Schedule urgent meeting at 6 PM and move Yoga to the next available slot on Monday")
        self.assertTrue(result["verified"])
        writes = [(n,a) for n,a in service.events().calls if n in {"patch","insert"}]
        self.assertEqual(writes[0][1]["body"]["start"]["dateTime"], "2026-09-14T08:00:00+05:30")
        self.assertEqual(writes[1][1]["body"]["start"]["dateTime"], "2026-09-13T18:00:00+05:30")

    def service(self, **kwargs):
        return FakeService(list_result={"items": [event("yoga", "Yoga",
            "2026-09-13T18:00:00+05:30", "2026-09-13T19:00:00+05:30")]}, **kwargs)

    def test_explicit_permission_moves_then_creates_without_extra_confirmation(self):
        service = self.service()
        result = CalendarConversation(None, service, planner=Planner(plan())).ask(
            "Schedule urgent meeting at 6 PM and move Yoga to the next available slot")
        self.assertTrue(result["verified"])
        writes = [(n, args) for n, args in service.events().calls if n in {"patch", "insert", "delete"}]
        self.assertEqual([n for n, _ in writes], ["patch", "insert"])
        self.assertEqual(writes[0][1]["body"]["start"]["dateTime"], "2026-09-13T19:00:00+05:30")
        self.assertIn("Moved Yoga", result["response"])

    def test_urgency_alone_requires_review_and_cancel_changes_nothing(self):
        service = self.service()
        chat = CalendarConversation(None, service, planner=Planner(plan("propose_relocation")))
        proposal = chat.ask("Schedule urgent meeting at 6 PM")
        self.assertTrue(proposal["awaiting_confirmation"])
        self.assertEqual(len(proposal["proposed_changes"]), 2)
        result = chat.ask("no")
        self.assertFalse(any(n in {"patch", "insert", "delete"} for n, _ in service.events().calls))
        self.assertIn("Cancelled", result["response"])

    def test_confirmed_plan_executes_after_rechecking(self):
        service = self.service()
        chat = CalendarConversation(None, service, planner=Planner(plan("propose_relocation")))
        chat.ask("Schedule urgent meeting at 6 PM")
        self.assertTrue(chat.ask("yes")["verified"])
        self.assertEqual([n for n, _ in service.events().calls], ["list", "list", "patch", "insert"])

    def test_changed_calendar_blocks_confirmed_plan(self):
        service = self.service()
        chat = CalendarConversation(None, service, planner=Planner(plan("propose_relocation")))
        chat.ask("Schedule urgent meeting at 6 PM")
        service.events()._list_result["items"].append(event("new", "New blocker", "2026-09-13T19:00:00+05:30", "2026-09-13T20:00:00+05:30"))
        result = chat.ask("yes")
        self.assertTrue(result["error"])
        self.assertFalse(any(n in {"patch", "insert"} for n, _ in service.events().calls))

    def test_failure_after_move_reports_partial_result(self):
        service = self.service(insert_error=HttpError(FakeResponse(403), b"forbidden"))
        result = CalendarConversation(None, service, planner=Planner(plan())).ask(
            "Schedule urgent meeting at 6 PM and move Yoga to the next available slot")
        self.assertFalse(result["verified"])
        self.assertIn("Completed: Moved Yoga", result["response"])
        self.assertIsNone(result["pending_action"])

    def test_no_space_never_moves_or_deletes(self):
        service = self.service()
        service.events()._list_result["items"].append(event("busy", "Busy", "2026-09-13T19:00:00+05:30", "2026-09-13T21:00:00+05:30"))
        result = CalendarConversation(None, service, planner=Planner(plan())).ask(
            "Schedule urgent meeting at 6 PM and move Yoga to the next available slot")
        self.assertIn("No replacement slot", result["response"])
        self.assertFalse(any(n in {"patch", "insert", "delete"} for n, _ in service.events().calls))

    def test_permission_for_yoga_does_not_include_another_event(self):
        service = self.service()
        service.events()._list_result["items"].append(event("other", "Other meeting", "2026-09-13T18:00:00+05:30", "2026-09-13T19:00:00+05:30"))
        result = CalendarConversation(None, service, planner=Planner(plan())).ask(
            "Schedule urgent meeting at 6 PM and move Yoga to the next available slot")
        self.assertTrue(result["awaiting_confirmation"])
        self.assertEqual(len(result["proposed_changes"]), 3)

    def test_negated_permission_never_automatically_moves(self):
        service = self.service()
        result = CalendarConversation(None, service, planner=Planner(plan())).ask(
            "Schedule urgent meeting at 6 PM but do not move Yoga to the next available slot")
        self.assertFalse(any(n in {"patch", "insert"} for n, _ in service.events().calls))
