import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

from fastapi.testclient import TestClient
from app.graph_agent import CalendarConversation, QueryPlan
from app.multiuser import create_multiuser_app, COOKIE
from app.user_store import UserStore
from app.preferences import SchedulingPreferences
from app.undo import undo_change
from app.calendar_service import _normalise_event
from tests.test_agent import SequentialPlanner, event
from tests.test_calendar_service import FakeService, FakeRequest


class ConversationImprovements(unittest.TestCase):
    def test_read_detour_preserves_duration_question_and_start(self):
        planner = SequentialPlanner(QueryPlan(intent='create', title='Yoga', start_time='2026-09-16T18:00:00+05:30'))
        service = FakeService()
        chat = CalendarConversation(None, service, planner=planner)
        chat.ask('Add Yoga tomorrow at 6 PM')
        read = chat.ask('show events today')
        self.assertIn('unfinished task', read['response'])
        self.assertEqual(read['pending_action']['title'], 'Yoga')
        result = chat.ask('45 minutes')
        self.assertTrue(result['verified'])
        inserted = next(body for name, body in service.events().calls if name == 'insert')['body']
        self.assertEqual(inserted['start']['dateTime'], '2026-09-16T18:00:00+05:30')
        self.assertEqual(inserted['end']['dateTime'], '2026-09-16T18:45:00+05:30')
        self.assertEqual(len(planner.calls), 1)

    def test_date_correction_retains_missing_duration(self):
        planner = SequentialPlanner(QueryPlan(intent='create', title='Yoga', start_time='2026-09-15T18:00:00+05:30'))
        chat = CalendarConversation(None, FakeService(), planner=planner)
        chat.ask('Add Yoga today at 6 PM')
        with patch('app.graph_agent.local_now', return_value=datetime.fromisoformat('2026-09-15T12:00:00+05:30')):
            result = chat.ask('actually tomorrow')
        self.assertEqual(result['pending_action']['start_time'], '2026-09-16T18:00:00+05:30')
        self.assertTrue(result['pending_action']['awaiting_duration'])
        self.assertEqual(len(planner.calls), 1)

    def test_preview_pauses_and_confirmation_executes_once(self):
        service = FakeService()
        chat = CalendarConversation(None, service, preferences={'preview_changes':True}, planner=SequentialPlanner(
            QueryPlan(intent='create', title='Yoga', start_time='2026-09-16T18:00:00+05:30')))
        plan = chat.ask('Schedule Yoga tomorrow at 6 PM for 1 hour')
        self.assertTrue(plan['awaiting_confirmation'])
        self.assertEqual(plan['proposed_changes'][0]['new_end'], '2026-09-16T19:00:00+05:30')
        self.assertFalse(any(name == 'insert' for name, _ in service.events().calls))
        result = chat.ask('yes')
        self.assertTrue(result['verified'])
        self.assertEqual(sum(name == 'insert' for name, _ in service.events().calls), 1)

    def test_complete_conversation_with_correction_and_interruption(self):
        service = FakeService()
        planner = SequentialPlanner(QueryPlan(intent='create',title='Yoga',start_time='2026-09-15T18:00:00+05:30'))
        chat = CalendarConversation(None,service,planner=planner,preferences={'preview_changes':True})
        chat.ask('Schedule Yoga today at 6 PM')
        proposed = chat.ask('for an hour')
        self.assertTrue(proposed['awaiting_confirmation'])
        with patch('app.graph_agent.local_now',return_value=datetime.fromisoformat('2026-09-15T12:00:00+05:30')):
            proposed = chat.ask('actually tomorrow')
        self.assertIn('2026-09-16',proposed['proposed_changes'][0]['new_start'])
        chat.ask('what events do I have today?')
        revised = chat.ask('Okay, back to Yoga—make it 45 minutes')
        self.assertTrue(revised['awaiting_confirmation'])
        self.assertEqual(revised['proposed_changes'][0]['new_end'],'2026-09-16T18:45:00+05:30')
        self.assertFalse(any(name=='insert' for name,_ in service.events().calls))
        self.assertTrue(chat.ask('yes')['verified'])
        self.assertEqual(len(planner.calls),1)

    def test_meridiem_answer_and_time_correction_preserve_title(self):
        service=FakeService()
        planner=SequentialPlanner(QueryPlan(intent='create',title='Yoga',start_time='2026-09-16T06:00:00+05:30'))
        chat=CalendarConversation(None,service,planner=planner,preferences={'preview_changes':True})
        chat.ask('Schedule Yoga tomorrow at 6')
        result=chat.ask('PM, for an hour')
        self.assertEqual(result['pending_action']['start_time'],'2026-09-16T18:00:00+05:30')
        result=chat.ask('actually 7 PM')
        self.assertEqual(result['pending_action']['end_time'],'2026-09-16T20:00:00+05:30')
        self.assertEqual(result['pending_action']['title'],'Yoga')
        self.assertEqual(len(planner.calls),1)

    def test_independent_task_can_be_suspended_and_resumed(self):
        chat=CalendarConversation(None,FakeService(),planner=SequentialPlanner(
            QueryPlan(intent='create',title='Yoga',start_time='2026-09-16T18:00:00+05:30'),
            QueryPlan(intent='create',title='DSA',start_time='2026-09-16T20:00:00+05:30')))
        chat.ask('Schedule Yoga tomorrow at 6 PM')
        chat.ask('Instead schedule DSA at 8 PM tomorrow')
        result=chat.ask('resume previous task')
        self.assertEqual(result['pending_action']['title'],'Yoga')
        self.assertTrue(result['pending_action']['awaiting_duration'])

    def test_undo_move_refuses_new_conflict_then_restores_time(self):
        before=_normalise_event(event('x','Yoga','2026-09-16T18:00:00+05:30','2026-09-16T19:00:00+05:30'))
        resource={**event('x','Yoga','2026-09-16T20:00:00+05:30','2026-09-16T21:00:00+05:30'),'etag':'v1'}
        service=FakeService(list_result={'items':[event('other','Meeting','2026-09-16T18:00:00+05:30','2026-09-16T19:00:00+05:30')]})
        service.events().get=lambda **kwargs:FakeRequest(resource)
        record={'kind':'update','before':before,'after':_normalise_event(resource)}
        with self.assertRaisesRegex(ValueError,'occupied'):
            undo_change(service,record)
        self.assertFalse(any(name=='patch' for name,_ in service.events().calls))
        service.events()._list_result={'items':[]}
        requests=[]
        def patch_request(**kwargs):
            requests.append(kwargs)
            operation=FakeRequest({})
            operation.headers={}
            return operation
        service.events().patch=patch_request
        self.assertIn('Undone',undo_change(service,record))
        self.assertEqual(requests[0]['body']['start']['dateTime'],before['start'])

    def test_custom_hours_extend_free_slot_search(self):
        chat = CalendarConversation(None,FakeService(),preferences={'work_start':6,'work_end':23},planner=SequentialPlanner(QueryPlan(intent='free_slot')))
        with patch('app.graph_agent.local_now',return_value=datetime.fromisoformat('2026-09-15T12:00:00+05:30')):
            result=chat.ask('Show available slots tomorrow after 9 PM')
        self.assertTrue(result['verified'])
        self.assertEqual(result['alternatives'][-1]['end'],'2026-09-16T23:00:00+05:30')

    def test_cancel_unfinished_task_without_model(self):
        service=FakeService()
        chat=CalendarConversation(None,service,planner=SequentialPlanner(QueryPlan(intent='create',title='Yoga',start_time='2026-09-16T18:00:00+05:30')))
        chat.ask('Schedule Yoga at 6 PM tomorrow')
        self.assertIsNone(chat.ask('cancel')['pending_action'])
        self.assertFalse(service.events().calls)

    def test_preferences_protect_event_and_reserve_breaks(self):
        service = FakeService(list_result={'items':[event('yoga','Yoga','2026-09-16T18:00:00+05:30','2026-09-16T19:00:00+05:30')]})
        chat = CalendarConversation(None, service, preferences={'protected_titles':['Yoga']}, planner=SequentialPlanner(QueryPlan(intent='delete',search_query='Yoga')))
        result = chat.ask('Delete Yoga tomorrow')
        self.assertIn('protected', result['response'])
        self.assertFalse(any(name=='delete' for name, _ in service.events().calls))
        preferences = SchedulingPreferences(break_minutes=15,study_start=18)
        buffered = preferences.busy_events([{'start':'2026-09-16T18:00:00+05:30','end':'2026-09-16T19:00:00+05:30'}])
        self.assertEqual(buffered[0]['start'],'2026-09-16T17:45:00+05:30')
        self.assertEqual(preferences.window(datetime(2026,9,16),'DSA practice')[0].hour,18)

    def test_older_event_facts_survive_read_turns(self):
        chat = CalendarConversation(None, FakeService(), planner=SequentialPlanner(QueryPlan(intent='create',title='Yoga',start_time='2026-09-16T18:00:00+05:30')))
        chat.ask('Schedule Yoga tomorrow at 6 PM for 1 hour')
        for _ in range(8):
            result = chat.ask('show events today')
        self.assertEqual(result['memory_summary'][0]['title'],'Yoga')

    def test_preferences_private_persistent_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserStore(directory)
            for user in ('alice','bob'):
                store.save_user(user,{'id':user},{})
            app = create_multiuser_app(store=store)
            alice, bob = TestClient(app), TestClient(app)
            alice.cookies.set(COOKIE,store.session('alice'))
            bob.cookies.set(COOKIE,store.session('bob'))
            self.assertEqual(TestClient(app).get('/preferences').status_code,401)
            self.assertEqual(alice.put('/preferences',json={'work_start':10,'work_end':20}).status_code,403)
            alice.headers['X-Task-Pilot']='1'
            self.assertEqual(alice.put('/preferences',json={'work_start':20,'work_end':10}).status_code,422)
            self.assertEqual(alice.put('/preferences',json={'work_start':10,'work_end':20,'protected_titles':['Yoga']}).status_code,200)
            self.assertEqual(UserStore(directory).preferences('alice')['protected_titles'],['Yoga'])
            self.assertEqual(bob.get('/preferences').json()['protected_titles'],[])

    def test_undo_requires_confirmation_and_checks_fresh_etag(self):
        service = FakeService()
        chat = CalendarConversation(None,service,planner=SequentialPlanner(QueryPlan(intent='create',title='Yoga',start_time='2026-09-16T18:00:00+05:30')))
        chat.ask('Schedule Yoga tomorrow at 6 PM for 1 hour')
        result = chat.ask('undo')
        self.assertTrue(result['undo_pending'])
        self.assertFalse(any(name=='delete' for name, _ in service.events().calls))
        self.assertFalse(chat.ask('no')['undo_pending'])

        resource = {**event('x','Yoga','2026-09-16T18:00:00+05:30','2026-09-16T19:00:00+05:30'),'etag':'version-1'}
        service.events().get = lambda **kwargs: FakeRequest(resource)
        operation = FakeRequest('')
        operation.headers = {}
        service.events().delete = lambda **kwargs: operation
        record = {'kind':'create','after':_normalise_event(resource)}
        resource['etag'] = 'version-2'
        with self.assertRaisesRegex(ValueError,'changed'):
            undo_change(service,record)
        resource['etag'] = 'version-1'
        self.assertIn('Undone', undo_change(service,record))
        self.assertEqual(operation.headers['If-Match'],'version-1')

    def test_cancel_undo_does_not_discard_unfinished_task(self):
        chat=CalendarConversation(None,FakeService(),planner=SequentialPlanner(QueryPlan(intent='create',title='DSA',start_time='2026-09-16T20:00:00+05:30')))
        chat.ask('Schedule DSA tomorrow at 8 PM')
        after=_normalise_event({**event('yoga','Yoga','2026-09-16T18:00:00+05:30','2026-09-16T19:00:00+05:30'),'etag':'v1'})
        chat.graph.update_state({'configurable':{'thread_id':'default'}},{'undo_record':{'kind':'create','after':after}})
        chat.ask('undo')
        cancelled=chat.ask('cancel')
        self.assertFalse(cancelled['undo_pending'])
        self.assertEqual(cancelled['pending_action']['title'],'DSA')
        self.assertEqual(cancelled['response'],'Undo cancelled.')


if __name__ == '__main__':
    unittest.main()
