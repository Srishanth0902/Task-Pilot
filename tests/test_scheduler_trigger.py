"""The external scheduler must not leak its bearer secret on redirects/errors."""
import io
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, MagicMock, patch
from urllib.error import HTTPError, URLError
from scripts.trigger_reminders import main, NoRedirect


class SchedulerTriggerTests(unittest.TestCase):
    def run_trigger(self, opener, **settings):
        output = io.StringIO()
        env = {'TASK_PILOT_URL': 'https://taskpilot.example', 'REMINDER_TRIGGER_SECRET': 's' * 32}
        env.update(settings)
        with patch.dict(os.environ, env), patch('scripts.trigger_reminders.build_opener', return_value=opener), \
                redirect_stdout(output), redirect_stderr(output):
            result = main()
        return result, output.getvalue()

    def test_success_posts_once_and_only_reports_counts(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"sent":2,"failed":0,"email":"private@example.com"}'
        code, output = self.run_trigger(opener)
        self.assertEqual(code, 0)
        self.assertNotIn('private@example.com', output)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + 's' * 32)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 240)
        self.assertEqual(opener.open.call_count, 1)

    def test_unsafe_origins_are_rejected_before_network(self):
        for origin in ['http://taskpilot.example', 'https://user:pass@taskpilot.example',
                       'https://taskpilot.example/path', 'https://taskpilot.example?token=secret']:
            opener = Mock()
            code, _ = self.run_trigger(opener, TASK_PILOT_URL=origin)
            self.assertEqual(code, 1)
            opener.open.assert_not_called()

    def test_network_errors_are_redacted_and_not_retried(self):
        for error in [HTTPError('https://private', 302, 'secret', None, None), URLError('s' * 32)]:
            opener = Mock()
            opener.open.side_effect = error
            code, output = self.run_trigger(opener)
            self.assertEqual(code, 1)
            self.assertNotIn('s' * 32, output)
            self.assertEqual(opener.open.call_count, 1)
        self.assertIsNone(NoRedirect().redirect_request(None, None, None, None, None, None))
