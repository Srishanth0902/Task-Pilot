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
    if os.getenv('DEPLOYMENT_MODE') == 'free':
        errors += free_configuration_errors()
    try:
        if not 1 <= int(os.getenv('PORT', '8000')) <= 65535:
            raise ValueError()
    except ValueError:
        errors.append('PORT must be a whole number between 1 and 65535.')
    try:
        if not 1 <= int(os.getenv('SESSION_MAX_AGE_DAYS', '30')) <= 365:
            raise ValueError()
    except ValueError:
        errors.append('SESSION_MAX_AGE_DAYS must be a whole number between 1 and 365.')
    for error in errors:
        print('NEEDS SETUP: '+error)
    if not errors:
        print('Configuration checks passed. This does not verify DNS, TLS, Google publishing status, provider capacity, or live sign-in.')
    return bool(errors)


def free_configuration_errors():
    """Offline validation; does not contact Neon, Brevo, or Google."""
    from urllib.parse import parse_qs
    errors = []
    url = urlparse(os.getenv('DATABASE_URL', ''))
    if url.scheme not in {'postgresql', 'postgres'} or not url.hostname:
        errors.append('Free hosting needs a PostgreSQL DATABASE_URL, such as the Neon connection string.')
    elif parse_qs(url.query).get('sslmode', [''])[0] not in {'require', 'verify-ca', 'verify-full'}:
        errors.append('Cloud DATABASE_URL must include sslmode=require or stronger.')
    if os.getenv('OPENROUTER_FREE_ONLY', 'true').lower() not in {'true', '1', 'yes'}:
        errors.append('Free deployment requires OPENROUTER_FREE_ONLY=true.')
    model = os.getenv('OPENROUTER_MODEL', 'qwen/qwen3.8-27b:free')
    if model != 'openrouter/free' and not model.endswith(':free'):
        errors.append('Choose openrouter/free or an available :free model.')
    provider = os.getenv('EMAIL_PROVIDER', '').strip().lower()
    if provider != 'disabled' and (provider != 'brevo' or not os.getenv('BREVO_API_KEY') or '@' not in os.getenv('EMAIL_SENDER', '')):
        errors.append('Reminders need EMAIL_PROVIDER=brevo, BREVO_API_KEY, and a verified EMAIL_SENDER.')
    if len(os.getenv('REMINDER_TRIGGER_SECRET', '')) < 32:
        errors.append('REMINDER_TRIGGER_SECRET must contain at least 32 characters.')
    if os.getenv('LOG_PRIVATE_CONTENT', '').lower() != 'false':
        errors.append('Set LOG_PRIVATE_CONTENT=false for cloud logs.')
    from app.usage import positive_limit
    for name, default, maximum in [('AI_REQUESTS_PER_DAY', 40, 40), ('AI_REQUESTS_PER_MINUTE', 10, 10),
                                  ('AI_USER_REQUESTS_PER_DAY', 10, 10), ('EMAIL_REQUESTS_PER_DAY', 250, 250),
                                  ('EMAIL_USER_REQUESTS_PER_DAY', 20, 250), ('CHAT_REQUESTS_PER_MINUTE', 20, 120)]:
        try:
            if positive_limit(name, default) > maximum:
                errors.append(name + ' exceeds the conservative free-pilot allowance.')
        except ValueError:
            errors.append(name + ' must be a positive whole number.')
    return errors


if __name__ == '__main__':
    raise SystemExit(main())
