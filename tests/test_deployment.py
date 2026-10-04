import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from cryptography.fernet import Fernet
from app.deployment_check import configuration_errors, main
from contextlib import redirect_stdout
from app.observability import log_workflow


class DeploymentTests(unittest.TestCase):
    def test_startup_checks_port_and_login_lifetime_before_listening(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'oauth.json'
            origin = 'https://pilot.acme.org'
            file.write_text(json.dumps({'web': {'client_id': 'synthetic', 'client_secret': 'synthetic',
                'redirect_uris': [origin + '/api/auth/callback']}}))
            env = {'PUBLIC_APP_URL': origin, 'TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode(),
                   'GOOGLE_WEB_CREDENTIALS_FILE': str(file), 'OPENROUTER_API_KEY': 'synthetic',
                   'DEPLOYMENT_MODE': 'local', 'PORT': '8000', 'SESSION_MAX_AGE_DAYS': '30'}
            with patch.dict('os.environ', env), patch('sys.argv', ['deployment_check']), redirect_stdout(io.StringIO()):
                self.assertFalse(main())
                for bad in [{'PORT': '0'}, {'PORT': 'invalid'}, {'PORT': '65536'},
                            {'SESSION_MAX_AGE_DAYS': '0'}, {'SESSION_MAX_AGE_DAYS': 'invalid'}]:
                    with patch.dict('os.environ', bad):
                        self.assertTrue(main())

    def test_production_configuration_requires_exact_callback_and_key(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'web.json'
            file.write_text(json.dumps({'web':{'client_id':'id','client_secret':'secret','redirect_uris':['https://calendar.acme.org/api/auth/callback']}}))
            self.assertEqual(configuration_errors('https://calendar.acme.org',Fernet.generate_key(),file,'test'),[])
            for origin in ['http://calendar.acme.org','https://example.com','https://calendar.acme.org/path','https://attacker@calendar.acme.org']:
                self.assertTrue(configuration_errors(origin,Fernet.generate_key(),file,'test'))
            self.assertTrue(configuration_errors('https://calendar.acme.org',None,file,'test'))
            self.assertTrue(configuration_errors('https://different.acme.org',Fernet.generate_key(),file,'test'))

    def test_production_logs_omit_calendar_and_message_content(self):
        logger=Mock()
        with patch.dict('os.environ',{'LOG_PRIVATE_CONTENT':'false'}),patch('app.observability._build_logger',return_value=logger):
            log_workflow('tool_output',thread_id='one',duration_ms=12,tool_output={'secret':'event title'},user_query='private request')
        payload=json.loads(logger.info.call_args.args[0])
        self.assertEqual(payload['duration_ms'],12)
        self.assertNotIn('tool_output',payload)
        self.assertNotIn('user_query',payload)
