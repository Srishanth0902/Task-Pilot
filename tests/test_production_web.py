"""Exercise the real built-app mounting contract with HTTPS browser cookies."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.user_store import UserStore
from app.multiuser import create_multiuser_app, COOKIE
from app.web import create_web_app


class ProductionWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.static = directory / 'dist'
        (self.static / 'assets').mkdir(parents=True)
        (self.static / 'index.html').write_text('<html>Task Pilot<script src="/assets/app-ab123.js"></script></html>')
        (self.static / 'assets' / 'app-ab123.js').write_text('console.log("built");')
        self.key = Fernet.generate_key()
        self.store = UserStore(directory / 'data', self.key)
        self.origin = 'https://pilot.acme.org'
        self.oauth = directory / 'google.web.json'
        self.oauth.write_text(json.dumps({'web': {'client_id': 'synthetic-id', 'client_secret': 'synthetic-secret',
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': [self.origin + '/api/auth/callback']}}))
        self.runtime = Mock()
        self.api = create_multiuser_app(self.store, self.runtime, origin=self.origin, oauth_file=self.oauth)
        with patch.dict(os.environ, {'PUBLIC_APP_URL': self.origin}):
            self.web = create_web_app(self.api, self.static)
        self.client = TestClient(self.web, base_url=self.origin)

    def test_ui_assets_api_and_unknown_api_do_not_mix(self):
        self.assertIn('Task Pilot', self.client.get('/').text)
        asset = self.client.get('/assets/app-ab123.js')
        self.assertEqual(asset.status_code, 200)
        self.assertIn('immutable', asset.headers['cache-control'])
        self.assertEqual(self.client.get('/api/health').json()['service'], 'task-pilot')
        self.assertEqual(self.client.get('/api/ready').json(), {'status': 'ready'})
        missing = self.client.get('/api/not-real')
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn('<html>', missing.text)
        self.assertEqual(self.client.get('/api/events').status_code, 401)

    def test_https_login_cookie_callback_and_logout(self):
        response = self.client.get('/api/auth/login', follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(response.headers['location']).query)
        self.assertEqual(query['redirect_uri'], [self.origin + '/api/auth/callback'])
        self.assertIn('Secure', response.headers['set-cookie'])
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.store.save_user('alice', {'email': 'alice@example.com'}, {'refresh_token': 'synthetic'})
        token = self.store.session('alice')
        self.client.cookies.set(COOKIE, token, domain='pilot.acme.org', path='/')
        response = self.client.get('/api/auth/me')
        self.assertTrue(response.json()['authenticated'])
        self.assertIn('Secure', response.headers['set-cookie'])
        self.assertEqual(self.client.post('/api/auth/logout', headers={'Origin': self.origin, 'X-Task-Pilot': '1'}).status_code, 200)
        self.assertFalse(self.client.get('/api/auth/me').json()['authenticated'])

    def test_user_records_survive_rebuilt_app_and_other_accounts_are_denied(self):
        self.store.save_user('alice', {'email': 'alice@example.com'}, {'refresh_token': 'synthetic'})
        self.store.save_user('bob', {'email': 'bob@example.com'}, {'refresh_token': 'synthetic'})
        self.store.conversation('alice', 'private', create=True)
        self.store.name_conversation('alice', 'private', 'Study timetable')
        reopened = UserStore(self.store.directory, self.key)
        fresh_api = create_multiuser_app(reopened, Mock(), origin=self.origin, oauth_file=self.oauth)
        client = TestClient(create_web_app(fresh_api, self.static), base_url=self.origin)
        client.cookies.set(COOKIE, self.store.session('alice'))
        self.assertEqual(client.get('/api/conversations/private').status_code, 200)
        client.cookies.clear()
        client.cookies.set(COOKIE, self.store.session('bob'))
        self.assertEqual(client.get('/api/conversations/private').status_code, 404)

    def test_ready_reports_storage_outage_without_credentials(self):
        with patch.object(self.store, 'db', side_effect=RuntimeError('private-database-password')):
            response = self.client.get('/api/ready')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-database-password', response.text)

    def test_public_responses_have_browser_security_headers(self):
        for path in ['/', '/assets/app-ab123.js', '/api/auth/me']:
            response = self.client.get(path)
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            self.assertEqual(response.headers['x-frame-options'], 'DENY')
            self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
            self.assertIn('max-age', response.headers['strict-transport-security'])
        self.assertEqual(self.client.get('/').headers['cache-control'], 'no-store')

    def test_missing_build_stops_startup(self):
        with self.assertRaisesRegex(ValueError, 'Built frontend is missing'):
            create_web_app(self.api, Path(self.temp.name) / 'not-built')

    def test_public_policy_pages_without_login(self):
        import shutil
        public = Path(__file__).resolve().parents[1] / 'frontend' / 'public'
        for page, heading in [('privacy', 'Privacy policy'), ('terms', 'Terms of use')]:
            shutil.copytree(public / page, self.static / page)
            response = self.client.get('/' + page + '/')
            self.assertEqual(response.status_code, 200)
            self.assertIn('<h1>' + heading + '</h1>', response.text)
            self.assertIn('netflixchill3007@gmail.com', response.text)
            self.assertIn('href="/"', response.text)
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(self.client.get('/api/events').status_code, 401)
