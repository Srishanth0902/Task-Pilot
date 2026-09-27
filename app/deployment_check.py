"""Read-only deployment validation. Never prints credential contents."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from app.config import PROJECT_ROOT


def configuration_errors(origin, key, credential_file, api_key, production=True):
    errors = []
    parsed = urlparse(origin)
    host = parsed.hostname or ''
    local = host in {'localhost', '127.0.0.1', '::1'}
    if parsed.scheme not in {'http','https'} or not host or parsed.username or parsed.password or parsed.path not in {'','/'} or parsed.query or parsed.fragment:
        errors.append('PUBLIC_APP_URL must be an origin only, such as https://calendar.your-domain.com.')
    if production and (parsed.scheme != 'https' or local or host.endswith('.example') or host == 'example.com'):
        errors.append('Production needs your actual public HTTPS domain.')
    if production:
        try:
            ipaddress.ip_address(host)
            errors.append('Use a public domain for the HTTPS deployment.')
        except ValueError:
            pass
    if production or key:
        try:
            Fernet(key or '')
        except (ValueError, TypeError):
            errors.append('TOKEN_ENCRYPTION_KEY must be a valid externally supplied Fernet key.')
    if not api_key:
        errors.append('OPENROUTER_API_KEY is missing.')
    try:
        config = json.loads(Path(credential_file).read_text())['web']
        if not config.get('client_id') or not config.get('client_secret'):
            raise ValueError()
        if origin.rstrip('/') + '/api/auth/callback' not in config.get('redirect_uris', []):
            errors.append('The OAuth file must include PUBLIC_APP_URL + /api/auth/callback; update Google Cloud and download the file again.')
    except (OSError, ValueError, KeyError, TypeError):
        errors.append('A valid Google Web application credentials JSON file is required.')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local',action='store_true',help='Validate localhost development instead of production')
    args = parser.parse_args()
    errors = configuration_errors(os.getenv('PUBLIC_APP_URL','http://127.0.0.1:5173'),os.getenv('TOKEN_ENCRYPTION_KEY'),
        os.getenv('GOOGLE_WEB_CREDENTIALS_FILE',str(PROJECT_ROOT/'credentials.web.json')),os.getenv('OPENROUTER_API_KEY'),not args.local)
    for error in errors:
        print('NEEDS SETUP: '+error)
    if not errors:
        print('Configuration checks passed. This does not verify DNS, TLS, Google publishing status, provider capacity, or live sign-in.')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
