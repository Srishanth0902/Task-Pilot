import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.user_store import UserStore
from app.usage import BudgetedModel
from app.storage import create_store
from app.reminders import BrevoMailer, create_mailer
from app.deployment_check import free_configuration_errors
from app.multiuser import create_multiuser_app, COOKIE


class FreeHostingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key = Fernet.generate_key()
        self.store = UserStore(self.temp.name, self.key)
        self.store.save_user('alice', {'email': 'alice@example.com'}, {'refresh_token': 'private'})

    def test_budgets_survive_restart_and_global_limit_is_atomic(self):
        def reserve(_):
            return self.store.consume_limits([('shared', 3, 60)], now=100) == 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(12))), 3)
        reopened = UserStore(self.temp.name, self.key)
        self.assertGreater(reopened.consume_limits([('shared', 3, 60)], now=110), 0)
        self.assertEqual(reopened.consume_limits([('shared', 3, 60)], now=121), 0)

    def test_failed_reservation_does_not_consume_other_users_budget(self):
        self.store.consume_limits([('alice', 1, 60)], now=100)
        self.assertGreater(self.store.consume_limits([('global', 1, 60), ('alice', 1, 60)], now=100), 0)
        self.assertEqual(self.store.consume_limits([('global', 1, 60), ('bob', 1, 60)], now=100), 0)

    def test_actual_planner_invocations_count_and_block_before_network(self):
        model = Mock()
        factory = Mock(return_value=model)
        planner = BudgetedModel(factory, self.store, 'alice').with_structured_output(dict)
        factory.assert_not_called()
        with patch.dict(os.environ, {'AI_USER_REQUESTS_PER_DAY': '1'}):
            planner.invoke([])
            with self.assertRaisesRegex(RuntimeError, 'allowance'):
                planner.invoke([])
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 1)

    def test_free_cloud_never_falls_back_to_ephemeral_sqlite(self):
        with patch.dict(os.environ, {'DEPLOYMENT_MODE': 'free', 'DATABASE_URL': ''}):
            with self.assertRaisesRegex(ValueError, 'DATABASE_URL'):
                create_store()

    def test_https_delivery_uses_fixed_endpoint_and_no_retry_or_secret_in_error(self):
        mailer = BrevoMailer('secret-api-key', 'verified@example.com')
        with patch('app.reminders.requests.post', return_value=Mock(status_code=201)) as post:
            self.assertTrue(mailer.send('alice@example.com', 'Due soon', 'Body'))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(post.call_args.kwargs['json']['to'], [{'email': 'alice@example.com'}])
        with patch('app.reminders.requests.post', return_value=Mock(status_code=429)):
            with self.assertRaisesRegex(RuntimeError, 'rejected') as error:
                mailer.send('alice@example.com', 'Due soon', 'Body')
        self.assertNotIn('secret-api-key', str(error.exception))

    def test_free_hosting_rejects_smtp(self):
        with patch.dict(os.environ, {'DEPLOYMENT_MODE': 'free', 'EMAIL_PROVIDER': 'smtp'}):
            with self.assertRaisesRegex(ValueError, 'SMTP'):
                create_mailer()

    def test_reminder_scheduler_requires_secret_not_user_cookie(self):
        app = create_multiuser_app(self.store, runtime=Mock())
        client = TestClient(app)
        with patch.dict(os.environ, {'REMINDER_TRIGGER_SECRET': 's' * 32}):
            self.assertEqual(client.post('/internal/reminders').status_code, 401)
            client.cookies.set(COOKIE, self.store.session('alice'))
            self.assertEqual(client.post('/internal/reminders').status_code, 401)
            with patch('app.multiuser.create_mailer', return_value=Mock(configured=True)), \
                 patch('app.multiuser.ReminderService') as service:
                service.return_value.sweep.return_value = {'sent': 1, 'failed': 0, 'skipped_accounts': 0}
                headers = {'Authorization': 'Bearer ' + 's' * 32}
                self.assertEqual(client.post('/internal/reminders', headers=headers).json()['sent'], 1)
                self.assertTrue(client.post('/internal/reminders', headers=headers).json()['already_checked'])
                self.assertEqual(service.return_value.sweep.call_count, 1)

    def test_chat_rate_limit_prevents_runtime_execution(self):
        runtime = Mock()
        client = TestClient(create_multiuser_app(self.store, runtime))
        client.cookies.set(COOKIE, self.store.session('alice'))
        client.headers['X-Task-Pilot'] = '1'
        with patch.dict(os.environ, {'CHAT_REQUESTS_PER_MINUTE': '1'}):
            self.store.consume_limits([('chat:user:alice', 1, 60)])
            response = client.post('/chat', json={'message': 'Please schedule Yoga'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        runtime.chat.assert_not_called()

    def test_preflight_requires_cloud_settings_and_free_allowances(self):
        settings = {'DATABASE_URL': 'postgresql://u:p@neon.example/db?sslmode=require',
                    'EMAIL_PROVIDER': 'brevo', 'BREVO_API_KEY': 'key', 'EMAIL_SENDER': 'a@example.com',
                    'REMINDER_TRIGGER_SECRET': 's' * 32, 'LOG_PRIVATE_CONTENT': 'false',
                    'OPENROUTER_MODEL': 'openrouter/free', 'OPENROUTER_FREE_ONLY': 'true'}
        with patch.dict(os.environ, settings):
            self.assertEqual(free_configuration_errors(), [])
            with patch.dict(os.environ, {'AI_REQUESTS_PER_DAY': '1000'}):
                self.assertTrue(free_configuration_errors())
