import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from cryptography.fernet import Fernet
from app.deployment_check import configuration_errors
from app.observability import log_workflow


class DeploymentTests(unittest.TestCase):
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
