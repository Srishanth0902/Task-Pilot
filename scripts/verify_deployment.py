"""Test the built production image against disposable TLS PostgreSQL.

Uses only synthetic credentials. Creates and removes its own named Docker
containers/network; never reads .env, connects to Google, or sends email.
Requires Docker Engine. Exit 2 means the environment is unverified, not passed.
"""
import argparse
import json
import re
import subprocess
import sys
import socket
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse, quote

import requests
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent.parent


def docker(*arguments, timeout=60):
    result = subprocess.run(['docker', *arguments], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        # Arguments can contain synthetic keys; never print them or docker logs.
        raise RuntimeError('Docker ' + arguments[0] + ' failed (exit ' + str(result.returncode) + ').')
    return result.stdout.strip()


def certificates(folder):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'db')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('db')]), critical=False)
        .sign(key, hashes.SHA256()))
    (folder / 'server.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / 'server.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def wait_ready(origin, seconds=120, process=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError('Production server exited before readiness.')
        try:
            if requests.get(origin + '/api/ready', timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError('Production image never became ready.')


def check_http(origin, public_origin):
    with requests.Session() as client:
        index = client.get(origin + '/', timeout=10)
        assert index.status_code == 200 and '<html' in index.text.lower(), 'Frontend HTML is missing'
        assets = re.findall(r'(?:src|href)="(/assets/[^"?]+)', index.text)
        assert assets, 'Built frontend references no assets'
        for asset in assets:
            response = client.get(origin + asset, timeout=10)
            assert response.status_code == 200, 'Frontend asset is missing'
            assert 'immutable' in response.headers.get('Cache-Control', ''), 'Assets are not cacheable'
        assert index.headers.get('Cache-Control') == 'no-store', 'HTML must not become stale after deploy'
        assert index.headers.get('X-Content-Type-Options') == 'nosniff'
        health = client.get(origin + '/api/health', timeout=10)
        assert health.status_code == 200 and health.json()['service'] == 'task-pilot'
        assert not client.get(origin + '/api/auth/me', timeout=10).json()['authenticated']
        for endpoint in ['/api/events', '/api/conversations', '/api/assignments']:
            assert client.get(origin + endpoint, timeout=10).status_code == 401, 'Anonymous access was allowed'
        login = client.get(origin + '/api/auth/login', timeout=10, allow_redirects=False)
        assert login.status_code == 307, 'OAuth entrypoint failed'
        query = parse_qs(urlparse(login.headers['Location']).query)
        assert query['redirect_uri'] == [public_origin + '/api/auth/callback'], 'OAuth callback is wrong'
        assert 'Secure' in login.headers['Set-Cookie'] and 'HttpOnly' in login.headers['Set-Cookie']


def native_smoke(record):
    """Run the exact production entrypoint when Docker isn't available.

    Requires a disposable localhost TEST_DATABASE_URL with TLS enabled. Uses
    its own UUID-named schema, not an existing app's users or conversations.
    This does not verify a Docker build or a remote hosting provider.
    """
    import os
    sys.path.insert(0, str(ROOT))
    import psycopg
    from psycopg import sql
    from app.postgres_store import PostgresUserStore
    url = os.getenv('TEST_DATABASE_URL', '')
    parsed = urlparse(url)
    if parsed.scheme not in {'postgres', 'postgresql'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Native smoke needs a disposable localhost TEST_DATABASE_URL, never a cloud account URL.')
    if parse_qs(parsed.query).get('sslmode', [''])[0] not in {'require', 'verify-ca', 'verify-full'}:
        raise ValueError('Native smoke TEST_DATABASE_URL must use TLS (sslmode=require or stronger).')
    schema = 'deployment_smoke_' + uuid.uuid4().hex
    with psycopg.connect(url) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    scoped_url = url + ('&' if '?' in url else '?') + 'options=' + quote('-c search_path=' + schema)
    process = None

    def stop():
        nonlocal process
        if process and process.poll() is None:
            if os.name == 'nt':
                # Windows venv launchers have a child Python process. Killing
                # only the launcher would leave the test server and log open.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=15, check=True)
            else:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        process = None

    try:
        with tempfile.TemporaryDirectory(prefix='taskpilot-native-smoke-') as temporary:
            folder = Path(temporary)
            public = 'https://taskpilot-ci.onrender.com'
            file = folder / 'credentials.web.json'
            file.write_text(json.dumps({'web': {'client_id': 'synthetic-client', 'client_secret': 'synthetic-secret',
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
                'redirect_uris': [public + '/api/auth/callback']}}))
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1', 0))
                port = port_socket.getsockname()[1]
            env = dict(os.environ, DEPLOYMENT_MODE='free', PUBLIC_APP_URL=public, PORT=str(port),
                DATABASE_URL=scoped_url, TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                GOOGLE_WEB_CREDENTIALS_FILE=str(file), OPENROUTER_API_KEY='synthetic-unused-key',
                OPENROUTER_FREE_ONLY='true', EMAIL_PROVIDER='brevo', BREVO_API_KEY='synthetic-unused-key',
                EMAIL_SENDER='smoke@example.com', REMINDER_TRIGGER_SECRET=uuid.uuid4().hex,
                LOG_PRIVATE_CONTENT='false', LOG_FILE=str(folder / 'workflow.jsonl'))
            origin = 'http://127.0.0.1:' + str(port)
            store = PostgresUserStore(scoped_url, env['TOKEN_ENCRYPTION_KEY'])
            log = folder / 'server.log'
            try:
                # Local-only process; public-origin cookie settings are tested,
                # but no actual TLS/domain or external OAuth exchange is claimed.
                with log.open('wb') as output:
                    process = subprocess.Popen([sys.executable, '-m', 'app.serve'], cwd=ROOT, env=env,
                                               stdout=output, stderr=output)
                    wait_ready(origin, process=process)
                    record('native_production_entrypoint_port_and_tls_postgresql', 'PASS')
                    check_http(origin, public)
                    record('native_frontend_assets_api_auth_and_oauth_callback', 'PASS')
                    store.save_user('smoke', {'email': 'smoke@example.com'}, {'refresh_token': 'synthetic'})
                    store.conversation('smoke', 'smoke-thread', create=True)
                    store.name_conversation('smoke', 'smoke-thread', 'Persistent synthetic conversation')
                    stop()
                    process = subprocess.Popen([sys.executable, '-m', 'app.serve'], cwd=ROOT, env=env,
                                               stdout=output, stderr=output)
                    wait_ready(origin, process=process)
                    fresh = PostgresUserStore(scoped_url, env['TOKEN_ENCRYPTION_KEY'])
                    assert fresh.user('smoke')[1]['refresh_token'] == 'synthetic'
                    assert fresh.conversations('smoke')[0]['title'] == 'Persistent synthetic conversation'
                    assert requests.get(origin + '/api/auth/me', timeout=10).json()['authenticated'] is False
                    record('native_encrypted_data_survives_process_restart', 'PASS')
            finally:
                stop()
    finally:
        stop()
        with psycopg.connect(url) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='task-pilot-render:ci')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--native', action='store_true', help='Test production entrypoint with an isolated localhost TLS database; does not verify Docker')
    args = parser.parse_args()
    report = {'scope': 'production-image, synthetic credentials only', 'checks': []}

    def record(name, status, detail=None):
        report['checks'].append({'name': name, 'status': status, **({'detail': detail} if detail else {})})
        print(status + ' ' + name + (': ' + detail if detail else ''), flush=True)

    if args.native:
        report['scope'] = 'native production entrypoint, synthetic credentials only (not Docker)'
        code = 0
        try:
            native_smoke(record)
        except Exception as error:
            record('native_production_smoke', 'FAIL', type(error).__name__)
            code = 1
        destination = ROOT / '.worker-results'
        destination.mkdir(exist_ok=True)
        (destination / 'deployment-native-smoke.json').write_text(json.dumps(report, indent=2))
        return code

    try:
        docker('info', '--format', '{{.ServerVersion}}', timeout=20)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        record('docker_engine', 'UNVERIFIED', 'Docker engine is unavailable; run this check in CI or with Docker running.')
        destination = ROOT / '.worker-results'
        destination.mkdir(exist_ok=True)
        (destination / 'deployment-smoke.json').write_text(json.dumps(report, indent=2))
        return 2
    record('docker_engine', 'PASS')
    suffix = uuid.uuid4().hex[:12]
    network, db_name, app_name = ['taskpilot-smoke-' + part + '-' + suffix for part in ('network', 'db', 'app')]
    exit_code = 0
    try:
        if args.build:
            docker('build', '-f', 'Dockerfile.render', '-t', args.image, '.', timeout=1200)
            record('production_image_build', 'PASS')
        docker('network', 'create', network)
        with tempfile.TemporaryDirectory(prefix='taskpilot-smoke-') as temporary:
            folder = Path(temporary)
            certificates(folder)
            public = 'https://taskpilot-ci.onrender.com'
            (folder / 'credentials.web.json').write_text(json.dumps({'web': {
                'client_id': 'synthetic-client', 'client_secret': 'synthetic-secret',
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
                'redirect_uris': [public + '/api/auth/callback']}}))
            password = uuid.uuid4().hex
            env = {'DEPLOYMENT_MODE': 'free', 'PUBLIC_APP_URL': public, 'PORT': '8123',
                'DATABASE_URL': f'postgresql://taskpilot:{password}@db:5432/taskpilot?sslmode=require',
                'TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode(),
                'GOOGLE_WEB_CREDENTIALS_FILE': '/etc/secrets/credentials.web.json',
                'OPENROUTER_API_KEY': 'synthetic-unused-key', 'OPENROUTER_FREE_ONLY': 'true',
                'EMAIL_PROVIDER': 'brevo', 'BREVO_API_KEY': 'synthetic-unused-key',
                'EMAIL_SENDER': 'smoke@example.com', 'REMINDER_TRIGGER_SECRET': uuid.uuid4().hex,
                'LOG_PRIVATE_CONTENT': 'false'}
            (folder / 'run.env').write_text('\n'.join(name + '=' + value for name, value in env.items()))
            mount = 'type=bind,source=' + str(folder) + ',target=/smoke,readonly'
            docker('run', '-d', '--name', db_name, '--network', network, '--network-alias', 'db',
                '--tmpfs', '/var/lib/postgresql/data:rw',
                '-e', 'POSTGRES_USER=taskpilot', '-e', 'POSTGRES_PASSWORD=' + password,
                '-e', 'POSTGRES_DB=taskpilot', '--mount', mount, '--entrypoint', 'sh', 'postgres:17', '-c',
                'cp /smoke/server.crt /tmp/server.crt && cp /smoke/server.key /tmp/server.key && '
                'chown postgres:postgres /tmp/server.crt /tmp/server.key && chmod 600 /tmp/server.key && '
                'exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key', timeout=180)
            # Require a real SQL connection, not just the temporary init server's socket.
            deadline = time.monotonic() + 90
            while True:
                try:
                    docker('exec', '-e', 'PGPASSWORD=' + password, db_name,
                           'psql', '-h', '127.0.0.1', '-U', 'taskpilot', '-d', 'taskpilot', '-c', 'SELECT 1', timeout=10)
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Disposable PostgreSQL never became ready.')
                    time.sleep(1)
            docker('run', '-d', '--name', app_name, '--network', network, '-p', '127.0.0.1::8123',
                '--memory', '512m', '--cpus', '0.1',
                '--env-file', str(folder / 'run.env'), '--mount',
                'type=bind,source=' + str(folder / 'credentials.web.json') + ',target=/etc/secrets/credentials.web.json,readonly', args.image)
            port = json.loads(docker('inspect', app_name))[0]['NetworkSettings']['Ports']['8123/tcp'][0]['HostPort']
            origin = 'http://127.0.0.1:' + port
            wait_ready(origin)
            docker('exec', app_name, 'python', '-c',
                   'import os; assert os.getuid()==1000 and os.getgid()==1000')
            record('production_startup_with_tls_postgresql_and_custom_port', 'PASS')
            check_http(origin, public)
            record('frontend_assets_api_auth_boundary_and_oauth_callback', 'PASS')
            docker('exec', app_name, 'python', '-c',
                "from app.storage import create_store; s=create_store(); s.save_user('smoke',{'email':'smoke@example.com'},{'refresh_token':'synthetic'}); "
                "s.conversation('smoke','smoke-thread',create=True); s.name_conversation('smoke','smoke-thread','Persistent synthetic conversation')")
            docker('restart', app_name)
            wait_ready(origin)
            docker('exec', app_name, 'python', '-c',
                "from app.storage import create_store; s=create_store(); assert s.user('smoke')[1]['refresh_token']=='synthetic'; "
                "assert s.conversations('smoke')[0]['title']=='Persistent synthetic conversation'")
            record('encrypted_user_and_conversation_survive_container_restart', 'PASS')
            docker('stop', db_name)
            assert requests.get(origin + '/api/ready', timeout=20).status_code == 503, 'Storage outage was reported healthy'
            record('storage_outage_not_reported_ready', 'PASS')
            # Remove containers before Windows releases the bind-mounted files.
            docker('rm', '-f', app_name, db_name)
    except (OSError, subprocess.TimeoutExpired, RuntimeError, AssertionError, requests.RequestException, KeyError, ValueError) as error:
        record('production_smoke', 'FAIL', str(error)[:200])
        exit_code = 1
    finally:
        # Exact UUID-owned targets only. No volumes or unrelated containers removed.
        subprocess.run(['docker', 'rm', '-f', app_name, db_name], capture_output=True, timeout=30)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=30)
        destination = ROOT / '.worker-results'
        destination.mkdir(exist_ok=True)
        (destination / 'deployment-smoke.json').write_text(json.dumps(report, indent=2))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
