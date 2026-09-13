import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import ChatRequest
from app.multiuser import COOKIE, UserRuntime, create_multiuser_app
from app.user_store import UserStore
from app.graph_agent import QueryPlan
from tests.test_advanced_agent import Planner, event
from tests.test_calendar_service import FakeService


class Model:
    def __init__(self, planner):
        self.planner = planner
    def with_structured_output(self,*args,**kwargs):
        return self.planner


class MultiuserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key = Fernet.generate_key()
        self.store = UserStore(self.temp.name,self.key)
        for uid in ['alice','bob']:
            self.store.save_user(uid, {'id':uid,'email':uid+'@example.com'}, {'refresh_token':'private-'+uid})
        self.services = {uid:FakeService(list_result={'items':[event(uid,uid+' meeting','2026-09-14T10:00:00+05:30','2026-09-14T11:00:00+05:30')]}) for uid in ['alice','bob']}
        self.runtime = UserRuntime(self.store,lambda:Model(Planner(QueryPlan(intent='list'))),self.services.__getitem__)
        self.app = create_multiuser_app(self.store,self.runtime,origin='http://127.0.0.1:5173')
        self.alice = self.client('alice')
        self.bob = self.client('bob')

    def client(self,uid):
        client = TestClient(self.app)
        client.cookies.set(COOKIE,self.store.session(uid))
        client.headers['X-Task-Pilot']='1'
        return client

    def test_anonymous_private_routes_and_csrf(self):
        anon = TestClient(self.app)
        for path in ['/events','/conversations','/conversations/private']:
            self.assertEqual(anon.get(path).status_code,401)
        self.assertEqual(anon.post('/chat',json={'message':'Hi'}).status_code,401)
        self.alice.headers['Origin']='https://attacker.example'
        self.assertEqual(self.alice.post('/chat',json={'message':'Hi'}).status_code,403)
        self.assertEqual(self.alice.post('/auth/logout').status_code,403)

    def test_each_calendar_uses_its_own_account(self):
        self.assertEqual(self.alice.get('/events').json()['events'][0]['event_id'],'alice')
        self.assertEqual(self.bob.get('/events').json()['events'][0]['event_id'],'bob')

    def test_refreshed_tokens_are_saved_only_for_the_owner(self):
        runtime=UserRuntime(self.store)
        credentials=Mock(valid=False,refresh_token='private-alice')
        credentials.to_json.return_value=json.dumps({'refresh_token':'renewed-alice','token':'new-access'})
        with patch('app.multiuser.Credentials.from_authorized_user_info',return_value=credentials), patch('app.multiuser.build',return_value='service'):
            self.assertEqual(runtime.service('alice'),'service')
        credentials.refresh.assert_called_once()
        self.assertEqual(self.store.user('alice')[1]['refresh_token'],'renewed-alice')
        self.assertEqual(self.store.user('bob')[1]['refresh_token'],'private-bob')

    def test_history_ownership_restart_encryption_and_logout(self):
        response=self.alice.post('/chat',json={'thread_id':'private','message':'Show my tasks tomorrow'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.bob.get('/conversations/private').status_code,404)
        self.assertEqual(self.bob.post('/chat',json={'thread_id':'private','message':'yes'}).status_code,404)
        self.assertEqual(self.bob.get('/conversations').json(),[])
        fresh=UserStore(self.temp.name,self.key)
        state,_=fresh.conversation('alice','private')
        self.assertIn('Show my tasks tomorrow',json.dumps(state))
        for path in fresh.directory.glob('*.sqlite3*'):
            content=path.read_bytes()
            self.assertNotIn(b'private-alice',content)
            self.assertNotIn(b'Show my tasks tomorrow',content)
        self.assertEqual(self.alice.post('/auth/logout').status_code,200)
        self.assertEqual(self.alice.get('/events').status_code,401)

    def test_confirmation_survives_new_runtime(self):
        self.runtime.model_factory=lambda:Model(Planner(QueryPlan(intent='delete',search_query='alice')))
        first=self.alice.post('/chat',json={'thread_id':'delete-thread','message':'Delete alice meeting'})
        self.assertTrue(first.json()['requires_confirmation'],first.text)
        fresh=UserRuntime(UserStore(self.temp.name,self.key),lambda:Model(Planner()),self.services.__getitem__)
        result=fresh.chat('alice','delete-thread','yes')
        self.assertTrue(result['verified'])
        self.assertEqual(sum(name=='delete' for name,_ in self.services['alice'].events().calls),1)

    def test_interrupted_request_does_not_replay_writes(self):
        self.store.conversation('alice','interrupted',create=True)
        self.store.save_conversation('alice','interrupted',busy=True)
        response=self.alice.post('/chat',json={'thread_id':'interrupted','message':'yes'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.services['alice'].events().calls,[])

    def test_oauth_state_is_expiring_and_single_use(self):
        state=self.store.oauth_start({'verifier':'secret'})
        self.assertEqual(self.store.oauth_finish(state),{'verifier':'secret'})
        self.assertIsNone(self.store.oauth_finish(state))
        self.assertEqual(self.alice.get('/auth/callback?state=invalid&code=code').status_code,400)
        with patch('app.user_store.time.time',return_value=0):
            expired=self.store.oauth_start({'nonce':'old'})
        self.assertIsNone(self.store.oauth_finish(expired))

    def test_google_callback_verified_identity_and_replay_protection(self):
        oauth_file=Path(self.temp.name)/'web.json'
        oauth_file.write_text(json.dumps({'web':{'client_id':'test-client'}}))
        app=create_multiuser_app(self.store,self.runtime,oauth_file=oauth_file)
        client=TestClient(app)
        oauth=Mock()
        oauth.client_config={'client_id':'test-client'}
        oauth.credentials.id_token='signed-id-token'
        oauth.credentials.to_json.return_value=json.dumps({'refresh_token':'new-secret'})
        oauth.oauth2session.token={'scope':'openid https://www.googleapis.com/auth/calendar'}
        state=self.store.oauth_start({'nonce':'nonce','verifier':'verifier'})
        client.cookies.set('task_pilot_oauth',state)
        with patch('app.multiuser.Flow.from_client_config',return_value=oauth), patch('app.multiuser.id_token.verify_oauth2_token',return_value={'sub':'carol','email':'carol@example.com','email_verified':True,'nonce':'nonce'}) as verify:
            response=client.get('/auth/callback',params={'state':state,'code':'code'},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        verify.assert_called_once()
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertEqual(client.get('/auth/me').json()['user']['id'],'carol')
        self.assertEqual(client.get('/auth/callback',params={'state':state,'code':'code'}).status_code,400)

    def test_google_callback_rejects_wrong_nonce(self):
        oauth_file=Path(self.temp.name)/'web.json'
        oauth_file.write_text(json.dumps({'web':{'client_id':'test-client'}}))
        client=TestClient(create_multiuser_app(self.store,self.runtime,oauth_file=oauth_file))
        oauth=Mock()
        oauth.client_config={'client_id':'test-client'}
        oauth.oauth2session.token={'scope':'https://www.googleapis.com/auth/calendar'}
        state=self.store.oauth_start({'nonce':'expected','verifier':'verifier'})
        client.cookies.set('task_pilot_oauth',state)
        with patch('app.multiuser.Flow.from_client_config',return_value=oauth), patch('app.multiuser.id_token.verify_oauth2_token',return_value={'sub':'attacker','email_verified':True,'nonce':'wrong'}):
            response=client.get('/auth/callback',params={'state':state,'code':'code'})
        self.assertEqual(response.status_code,400)
        self.assertFalse(client.get('/auth/me').json()['authenticated'])

    def test_independent_conversation_runs_while_another_waits(self):
        entered,release=threading.Event(),threading.Event()
        class SlowPlanner:
            def invoke(self,messages):
                if 'slow' in messages[-1].content:
                    entered.set()
                    if not release.wait(10): raise TimeoutError()
                return QueryPlan(intent='list')
        self.runtime.model_factory=lambda:Model(SlowPlanner())
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow=pool.submit(self.runtime.chat,'alice','slow-thread','slow request')
            self.assertTrue(entered.wait(5))
            try:
                fast=pool.submit(self.runtime.chat,'bob','fast-thread','Show my tasks tomorrow')
                self.assertTrue(fast.result(timeout=5)['verified'])
            finally:
                release.set()
            self.assertTrue(slow.result(timeout=5)['verified'])

    def test_same_conversation_lock_serializes_and_releases(self):
        entered=threading.Event()
        def acquire():
            with self.store.lock('conversation','same'):
                entered.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store.lock('conversation','same'):
                future=pool.submit(acquire)
                self.assertFalse(entered.wait(.1))
            future.result(timeout=3)
        self.assertTrue(entered.is_set())
