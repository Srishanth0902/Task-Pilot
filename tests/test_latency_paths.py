import unittest
from unittest.mock import Mock, patch

from app.graph_agent import CalendarConversation, QueryPlan
from tests.test_calendar_service import FakeService
from tests.test_conversation_repairs import NOW
from tests.test_advanced_agent import event


class LatencyPathTests(unittest.TestCase):
    @patch('app.graph_agent.local_now', return_value=NOW)
    def test_incomplete_move_rejects_invented_midnight(self, _):
        planner = Mock()
        planner.invoke.return_value = QueryPlan(intent='update', search_query='Yoga', start_time='2026-09-13T00:00:00+05:30')
        service = FakeService(list_result={'items': [event('yoga', 'Yoga', '2026-09-13T07:00:00+05:30', '2026-09-13T08:00:00+05:30')]})
        result = CalendarConversation(None, service, planner=planner).ask('Move my Yoga tomorrow')
        self.assertTrue(result.get('clarification'))
        self.assertFalse(any(n == 'patch' for n,_ in service.events().calls))

    @patch('app.graph_agent.local_now', return_value=NOW)
    def test_model_misclassifications_retain_filters_and_bulk_confirmation(self, _):
        for query, plan, intent, count in [
            ('Find all my study sessions tomorrow', QueryPlan(intent='list'), 'search', 2),
            ('Delete all my events tomorrow', QueryPlan(intent='delete'), 'bulk_delete', 3),
            ('Move my meeting tomorrow', QueryPlan(intent='update', search_query='my meeting'), 'update', 0),
        ]:
            planner = Mock()
            planner.invoke.return_value = plan
            items = [event(str(i), title, '2026-09-13T10:00:00+05:30', '2026-09-13T11:00:00+05:30') for i,title in enumerate(['DSA study', 'ML study', 'meeting'])]
            service = FakeService(list_result={'items':items})
            result = CalendarConversation(None, service, planner=planner).ask(query)
            self.assertEqual(result['intent'], intent)
            if intent == 'search':
                self.assertEqual(service.events().calls[0][1]['q'], 'study')
            elif intent == 'bulk_delete':
                self.assertTrue(result['awaiting_confirmation'])
                self.assertEqual(len(result['proposed_changes']), count)
            else:
                self.assertEqual(service.events().calls[0][1]['q'], 'meeting')
            self.assertFalse(any(n in {'insert', 'patch', 'delete'} for n,_ in service.events().calls))

    @patch('app.graph_agent.local_now', return_value=NOW)
    def test_exact_reads_skip_model_but_still_fetch_calendar(self, _):
        for query in ['Show my tasks tomorrow', 'List events today', 'Show me events for tomorrow.']:
            planner = Mock()
            service = FakeService()
            state = CalendarConversation(None, service, planner=planner).ask(query)
            planner.invoke.assert_not_called()
            self.assertTrue(state['verified'])
            self.assertEqual(service.events().calls[0][0], 'list')

    def test_qualified_or_mutating_requests_do_not_use_shortcut(self):
        for query in ['Show my tasks tomorrow after 6 PM', 'List events today and delete them', 'Show my study tasks tomorrow']:
            planner = Mock()
            planner.invoke.return_value = QueryPlan(intent='unknown')
            CalendarConversation(None, FakeService(), planner=planner).ask(query)
            planner.invoke.assert_called_once()

    def test_node_timings_are_logged(self):
        with patch('app.graph_agent.log_workflow') as logger:
            CalendarConversation(None, FakeService(), planner=Mock()).ask('List events today')
        timings = [call.kwargs['duration_ms'] for call in logger.call_args_list if call.args[0] == 'graph_node_finished']
        self.assertTrue(timings)
        self.assertTrue(all(t >= 0 for t in timings))
