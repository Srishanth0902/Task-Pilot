import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from cryptography import x509
from scripts import verify_deployment as runner


class DeploymentRunnerTests(unittest.TestCase):
    def test_restart_uses_new_ephemeral_host_port(self):
        mapping = json.dumps([{'NetworkSettings': {'Ports': {
            '8123/tcp': [{'HostPort': '45678'}]}}}])
        with patch.object(runner, 'docker', side_effect=['container', mapping]) as docker, \
                patch.object(runner, 'wait_ready') as ready:
            self.assertEqual(runner.restart_container('owned-container'), 'http://127.0.0.1:45678')
        self.assertEqual(docker.call_args_list[0].args, ('restart', 'owned-container'))
        self.assertEqual(docker.call_args_list[1].args, ('inspect', 'owned-container'))
        ready.assert_called_once_with('http://127.0.0.1:45678')

    def test_no_docker_is_unverified_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'ROOT', Path(directory)), \
                patch.object(runner, 'docker', side_effect=RuntimeError('engine unavailable')) as docker, \
                patch('sys.argv', ['verify_deployment']), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 2)
            report = json.loads((Path(directory) / '.worker-results' / 'deployment-smoke.json').read_text())
            self.assertEqual(report['checks'][0]['status'], 'UNVERIFIED')
            self.assertEqual(docker.call_count, 1)

    def test_failed_command_does_not_print_arguments_or_provider_output(self):
        result = Mock(returncode=1, stdout='private-value', stderr='private-value')
        with patch.object(runner.subprocess, 'run', return_value=result):
            with self.assertRaises(RuntimeError) as error:
                runner.docker('run', '-e', 'SECRET=private-value')
        self.assertNotIn('private-value', str(error.exception))

    def test_exited_production_server_is_not_mistaken_for_a_ready_old_server(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(runner.requests, 'get') as request:
            with self.assertRaisesRegex(RuntimeError, 'exited'):
                runner.wait_ready('http://127.0.0.1:9999', process=process)
        request.assert_not_called()

    def test_generated_certificate_has_only_disposable_database_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            runner.certificates(Path(directory))
            cert = x509.load_pem_x509_certificate((Path(directory) / 'server.crt').read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual(san.get_values_for_type(x509.DNSName), ['db'])
