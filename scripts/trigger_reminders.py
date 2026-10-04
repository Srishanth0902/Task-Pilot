"""Call the deployed reminder endpoint without logging secrets or following redirects."""
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    origin = os.getenv('TASK_PILOT_URL', '').rstrip('/')
    secret = os.getenv('REMINDER_TRIGGER_SECRET', '')
    parsed = urlparse(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment or len(secret) < 32:
        print('Scheduler needs an HTTPS origin and a secret of at least 32 characters.', file=sys.stderr)
        return 1
    request = Request(origin + '/api/internal/reminders', data=b'', method='POST',
                      headers={'Authorization': 'Bearer ' + secret})
    try:
        with build_opener(NoRedirect()).open(request, timeout=240) as response:
            result = json.loads(response.read(16384))
        print(json.dumps({key: result.get(key, 0) for key in ('sent', 'failed', 'skipped_accounts')}))
        return int(bool(result.get('failed')))
    except HTTPError as error:
        print(f'Reminder trigger returned HTTP {error.code}.', file=sys.stderr)
    except (URLError, TimeoutError, ValueError, OSError):
        print('Reminder trigger could not be confirmed. No automatic retry was made.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
