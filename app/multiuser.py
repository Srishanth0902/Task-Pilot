"""Authenticated HTTP application; no shared Desktop OAuth token fallback."""
import json
import os
from datetime import timedelta
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from langchain_core.messages import messages_from_dict, messages_to_dict

from app.config import PROJECT_ROOT, OPENROUTER_MODEL, TIMEZONE, OPENROUTER_API_KEY
from app.calendar_service import get_events
from app.date_utils import coerce_datetime, local_now
from app.graph_agent import CalendarConversation
from app.llm import create_openrouter_model
from app.storage import create_store
from app.usage import BudgetedModel, chat_allowance
from app.preferences import SchedulingPreferences
from app.assignments import Assignment
from app.study_planner import plan_sessions, progress, unschedulable
from app.reminders import create_mailer, ReminderService
from app.calendar_export import to_csv, to_ics
from app.calendar_provider import (
    GOOGLE,
    NATIVE,
    PROVIDER_LABELS,
    GoogleCalendarProvider,
    NativeCalendarProvider,
)

COOKIE = 'task_pilot_session'
CALENDAR_EVENT_SCOPE = 'https://www.googleapis.com/auth/calendar.events'
SCOPES = ['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile', CALENDAR_EVENT_SCOPE]


def _session_max_age(value=None):
    """Return the renewable login lifetime, bounded to 1-365 days."""
    raw = value if value is not None else os.getenv('SESSION_MAX_AGE_DAYS', '30')
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValueError('SESSION_MAX_AGE_DAYS must be a whole number') from None
    if not 1 <= days <= 365:
        raise ValueError('SESSION_MAX_AGE_DAYS must be between 1 and 365')
    return days * 24 * 60 * 60


class UserRuntime:
    def __init__(self, store, model_factory=create_openrouter_model, service_factory=None):
        self.store = store
        self.model_factory = model_factory
        self.service_factory = service_factory

    def service(self, user_id):
        if self.service_factory:
            return self.service_factory(user_id)
        # A separate HTTP transport per request: Google's httplib2 client is not
        # thread safe. Serialize only token refresh, never the entire chat.
        with self.store.lock('tokens', user_id):
            profile, data = self.store.user(user_id)
            creds = Credentials.from_authorized_user_info(data)
            if not creds.valid:
                if not creds.refresh_token:
                    raise HTTPException(401, 'Please sign in with Google again.')
                try:
                    creds.refresh(GoogleRequest())
                except Exception:
                    raise HTTPException(401, 'Google access expired. Please sign in again.') from None
                self.store.save_user(user_id, profile, json.loads(creds.to_json()))
        return build('calendar', 'v3', credentials=creds, cache_discovery=False)

    def active_provider_name(self, user_id):
        """Resolve which calendar this workspace uses. Server-side only.

        The browser never gets a say: it may ask to switch, but the stored
        choice and the stored credentials decide. An account with no explicit
        choice uses Google when it is connected, and the native calendar
        otherwise.
        """
        return self.store.active_calendar(user_id)

    def provider(self, user_id, name=None):
        """Build the active calendar provider for this workspace.

        When Google is the active calendar and its credentials are missing or
        expired, this raises. It never quietly hands back the native calendar:
        a silent downgrade would write the user's events somewhere they did not
        ask for and did not expect to find them.
        """
        name = name or self.active_provider_name(user_id)
        if name == NATIVE:
            return NativeCalendarProvider(self.store, user_id)
        return GoogleCalendarProvider(self.service(user_id))

    def chat(self, user_id, thread_id, message):
        with self.store.lock('conversation', thread_id):
            saved, interrupted = self.store.conversation(user_id, thread_id, create=True)
            if interrupted:
                raise HTTPException(409, 'The previous request was interrupted. Check your calendar and start a new conversation before making more changes.')
            self.store.name_conversation(user_id, thread_id, message)
            provider_name = self.active_provider_name(user_id)
            # A pending confirmation belongs to the calendar it was planned
            # against. If the active calendar changed while it was waiting,
            # answering "yes" must not execute it somewhere else, so the stale
            # plan is dropped and the user is told why.
            if saved and saved.get('awaiting_confirmation'):
                planned_on = saved.get('provider')
                if planned_on and planned_on != provider_name:
                    self.store.save_conversation(user_id, thread_id, {}, busy=False)
                    raise HTTPException(
                        409,
                        'Your calendar changed while that action was waiting for '
                        f'confirmation. It was prepared for {PROVIDER_LABELS.get(planned_on, planned_on)} '
                        f'but you are now using {PROVIDER_LABELS.get(provider_name, provider_name)}. '
                        'Nothing was changed — please ask again.',
                    )
            service = self.provider(user_id, provider_name)
            try:
                chat = CalendarConversation(BudgetedModel(self.model_factory, self.store, user_id), service,
                                            preferences=self.store.preferences(user_id))
                if saved:
                    saved['messages'] = messages_from_dict(saved.get('messages', []))
                    chat.graph.update_state({'configurable': {'thread_id': thread_id}}, saved)
                # A crash must never lead to automatically replaying a calendar write.
                self.store.save_conversation(user_id, thread_id, busy=True)
                result = chat.ask(message, thread_id=thread_id)
                result['provider'] = provider_name
                serial = dict(result, messages=messages_to_dict(result.get('messages', [])))
                self.store.save_conversation(user_id, thread_id, serial)
                return result
            finally:
                if hasattr(service, 'close'):
                    service.close()


def create_multiuser_app(store=None, runtime=None, *, origin=None, oauth_file=None, session_days=None):
    # Imported here to reuse the established response contract without a cycle.
    from app.api import ChatRequest, ChatResponse, EventsResponse, _chat_response
    origin = (origin or os.getenv('PUBLIC_APP_URL', 'http://127.0.0.1:5173')).rstrip('/')
    secure = urlparse(origin).scheme == 'https'
    if not secure and urlparse(origin).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('PUBLIC_APP_URL must use HTTPS outside localhost')
    key = os.getenv('TOKEN_ENCRYPTION_KEY')
    if secure and not key and store is None:
        raise ValueError('TOKEN_ENCRYPTION_KEY is required for deployment')
    store = store or create_store()
    runtime = runtime or UserRuntime(store)
    oauth_file = Path(oauth_file or os.getenv('GOOGLE_WEB_CREDENTIALS_FILE', str(PROJECT_ROOT / 'credentials.web.json')))
    redirect_uri = origin + '/api/auth/callback'
    session_max_age = _session_max_age(session_days)
    app = FastAPI(title='Task Pilot', version='2.0.0')
    app.state.store, app.state.runtime = store, runtime

    @app.middleware('http')
    async def private_headers(request, call_next):
        session_token = request.cookies.get(COOKIE, '')
        response = await call_next(request)
        # Active use renews both the server-side expiry and the persistent
        # browser cookie. Logout deletes the database row first, so it cannot
        # accidentally be renewed here.
        if session_token and store.touch_session(session_token, session_max_age):
            response.set_cookie(
                COOKIE, session_token, max_age=session_max_age,
                httponly=True, secure=secure, samesite='lax', path='/'
            )
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def current_user(request: Request):
        user_id = store.session_user(request.cookies.get(COOKIE, ''))
        if not user_id:
            raise HTTPException(401, 'Sign in with Google to continue.')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if request.headers.get('origin') not in {None, origin} or request.headers.get('x-task-pilot') != '1':
                raise HTTPException(403, 'Invalid request origin.')
        return user_id

    def flow(**kwargs):
        if not oauth_file.exists():
            raise HTTPException(503, 'Google web sign-in is not configured yet.')
        config = json.loads(oauth_file.read_text())
        if 'web' not in config:
            raise HTTPException(503, 'Google sign-in requires Web application OAuth credentials.')
        return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri, **kwargs)

    @app.get('/health')
    def health():
        return {'status': 'ok', 'service':'task-pilot', 'model':OPENROUTER_MODEL, 'timezone':TIMEZONE,
                'openrouter_configured': bool(OPENROUTER_API_KEY), 'google_credentials_configured':oauth_file.exists(),
                'google_token_configured':False, 'authentication_required':True,
                'email_reminders_configured': create_mailer().configured}

    @app.get('/ready')
    def ready():
        # The host must not route users to a service whose durable DB is down.
        # This deliberately avoids provider calls or any private account data.
        try:
            with store.db() as db:
                db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
        except Exception:
            raise HTTPException(503, 'Persistent storage is temporarily unavailable.') from None
        return {'status': 'ready'}

    @app.get('/auth/me')
    def me(request: Request):
        user_id = store.session_user(request.cookies.get(COOKIE, ''))
        payload = {'authenticated': bool(user_id),
                   'user': store.user(user_id)[0] if user_id else None,
                   'login_configured': oauth_file.exists()}
        if user_id:
            payload.update(_calendar_status(user_id))
        return payload

    def _reconnect_hint(provider):
        if provider.name == GOOGLE:
            return 'Try reconnecting your Google account, or switch to the Task Pilot calendar.'
        return 'Please try again.'

    def _calendar_status(user_id):
        """What calendar this workspace is on, and what else it could use."""
        connected = store.google_connected(user_id)
        active = store.active_calendar(user_id)
        return {
            'guest': store.is_guest(user_id),
            'google_connected': connected,
            'active_provider': active,
            'active_provider_label': PROVIDER_LABELS.get(active, active),
            'available_providers': (
                [{'name': GOOGLE, 'label': PROVIDER_LABELS[GOOGLE], 'ready': connected}]
                if oauth_file.exists() or connected else []
            ) + [{'name': NATIVE, 'label': PROVIDER_LABELS[NATIVE], 'ready': True}],
        }

    @app.post('/auth/guest')
    def start_guest(request: Request):
        """Open a workspace with no Google account behind it.

        The identity is a fresh opaque server-issued session; nothing the
        browser sends decides who the guest is. Each guest gets its own
        workspace rather than a shared anonymous account.
        """
        existing = store.session_user(request.cookies.get(COOKIE, ''))
        if existing:
            # Already signed in: keep that workspace rather than stranding its
            # data behind a brand new guest identity.
            return JSONResponse({'success': True, **_calendar_status(existing)})
        user_id = store.create_guest()
        response = JSONResponse({'success': True, **_calendar_status(user_id)})
        response.set_cookie(
            COOKIE, store.session(user_id, session_max_age), max_age=session_max_age,
            httponly=True, secure=secure, samesite='lax', path='/',
        )
        return response

    @app.get('/calendar/status')
    def calendar_status(user_id=Depends(current_user)):
        return _calendar_status(user_id)

    @app.put('/calendar/provider')
    def choose_calendar(provider: str = Query(...), user_id=Depends(current_user)):
        """Switch this workspace between the available calendars.

        Choosing Google without a connected account is refused rather than
        silently honoured, so the UI cannot leave someone pointed at a calendar
        that cannot answer.
        """
        if provider not in {GOOGLE, NATIVE}:
            raise HTTPException(422, 'Unknown calendar.')
        if provider == GOOGLE and not store.google_connected(user_id):
            raise HTTPException(
                409, 'Connect your Google account before switching to Google Calendar.'
            )
        store.set_calendar_provider(user_id, provider)
        return _calendar_status(user_id)

    @app.get('/auth/login')
    def login(request: Request):
        if store.consume_limits([('login:' + (request.client.host if request.client else 'unknown'), 10, 60)]):
            raise HTTPException(429, 'Too many sign-in attempts. Try again shortly.', headers={'Retry-After': '60'})
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        oauth = flow(code_verifier=verifier)
        state = store.oauth_start({'nonce':nonce, 'verifier':verifier})
        url, _ = oauth.authorization_url(
            state=state,
            nonce=nonce,
            access_type='offline',
            prompt='select_account consent',
            # Request only this client's current scopes, rather than merging
            # historical broad-calendar or desktop metadata grants into a
            # new web login token.
            include_granted_scopes='false',
            code_challenge_method='S256',
        )
        response = RedirectResponse(url)
        response.set_cookie('task_pilot_oauth', state, max_age=600, httponly=True, secure=secure, samesite='lax', path='/')
        return response

    @app.get('/auth/callback')
    def callback(request: Request, state: str = '', code: str = ''):
        cookie = request.cookies.get('task_pilot_oauth', '')
        if not state or not cookie or not secrets.compare_digest(state, cookie):
            raise HTTPException(400, 'Invalid or expired Google sign-in. Try signing in again.')
        pending = store.oauth_finish(state)
        if not pending or not code:
            raise HTTPException(400, 'Google sign-in expired or was declined. Try again.')
        try:
            oauth = flow(state=state, code_verifier=pending['verifier'])
            oauth.fetch_token(code=code)
            granted = oauth.oauth2session.token.get('scope', [])
            if isinstance(granted, str):
                granted = granted.split()
            if CALENDAR_EVENT_SCOPE not in granted:
                raise ValueError('Calendar access not granted')
            claims = id_token.verify_oauth2_token(oauth.credentials.id_token, GoogleRequest(), oauth.client_config['client_id'])
            if claims.get('nonce') != pending['nonce'] or not claims.get('email_verified') or not claims.get('sub'):
                raise ValueError('Invalid identity')
            user_id = claims['sub']
            allowed = {value.strip().casefold() for value in os.getenv('ALLOWED_GOOGLE_EMAILS', '').split(',') if value.strip()}
            if allowed and claims['email'].casefold() not in allowed:
                raise ValueError('This account is not invited to this pilot')
            with store.lock('tokens', user_id):
                tokens = json.loads(oauth.credentials.to_json())
                if not tokens.get('refresh_token'):
                    try:
                        tokens['refresh_token'] = store.user(user_id)[1].get('refresh_token')
                    except KeyError:
                        pass
                if not tokens.get('refresh_token'):
                    raise ValueError('Offline access not granted')
                store.save_user(
                    user_id,
                    {
                        'id': user_id,
                        'email': claims['email'],
                        'name': claims.get('name', claims['email']),
                        'picture': claims.get('picture'),
                    },
                    tokens,
                )
        except Exception:
            raise HTTPException(400, 'Google sign-in could not be completed. Please grant Calendar access and try again.') from None
        store.logout(request.cookies.get(COOKIE, ''))
        response = RedirectResponse(origin, status_code=303)
        response.set_cookie(COOKIE, store.session(user_id, session_max_age), max_age=session_max_age, httponly=True, secure=secure, samesite='lax', path='/')
        response.delete_cookie('task_pilot_oauth', path='/')
        return response

    @app.post('/auth/logout')
    def logout(request: Request, user_id=Depends(current_user)):
        store.logout(request.cookies.get(COOKIE, ''))
        from fastapi.responses import JSONResponse
        response = JSONResponse({'success':True})
        response.delete_cookie(COOKIE, path='/', secure=secure, httponly=True, samesite='lax')
        return response

    @app.get('/conversations')
    def conversations(user_id=Depends(current_user)):
        return store.conversations(user_id)

    @app.get('/preferences')
    def preferences(user_id=Depends(current_user)):
        return store.preferences(user_id)

    @app.put('/preferences')
    def save_preferences(body: SchedulingPreferences, user_id=Depends(current_user)):
        return store.save_preferences(user_id, body.model_dump())

    @app.get('/conversations/{thread_id}')
    def history(thread_id: str, user_id=Depends(current_user)):
        try:
            saved, busy = store.conversation(user_id, thread_id)
        except PermissionError:
            raise HTTPException(404, 'Conversation not found') from None
        saved = saved or {}
        return {'thread_id':thread_id, 'interrupted':busy,
                'messages':[{'role':'user' if m['type']=='human' else 'assistant', 'text':m['data']['content']} for m in saved.get('messages',[]) if m['type'] in {'human','ai'}],
                'latest':_chat_response(saved,thread_id).model_dump() if saved else None}

    @app.post('/chat', response_model=ChatResponse)
    def chat(body: ChatRequest, user_id=Depends(current_user)):
        chat_allowance(store, user_id)
        thread_id = body.thread_id or str(uuid.uuid4())
        try:
            return _chat_response(runtime.chat(user_id,thread_id,body.message),thread_id)
        except PermissionError:
            raise HTTPException(404, 'Conversation not found') from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'The request could not finish. Check your calendar before retrying any changes.') from None

    @app.get('/assignments')
    def list_assignments(include_done: bool = Query(True), user_id=Depends(current_user)):
        return store.assignments(user_id, include_done=include_done)

    @app.post('/assignments')
    def create_assignment(body: Assignment, user_id=Depends(current_user)):
        return store.save_assignment(user_id, body.model_dump())

    @app.put('/assignments/{assignment_id}')
    def update_assignment(assignment_id: str, body: Assignment, user_id=Depends(current_user)):
        try:
            return store.save_assignment(user_id, body.model_dump(), assignment_id=assignment_id)
        except PermissionError:
            raise HTTPException(404, 'Assignment not found') from None

    @app.delete('/assignments/{assignment_id}')
    def remove_assignment(assignment_id: str, user_id=Depends(current_user)):
        if not store.delete_assignment(user_id, assignment_id):
            raise HTTPException(404, 'Assignment not found')
        return {'deleted': True, 'id': assignment_id}

    def _calendar_events(user_id, days):
        """Read the window the planner needs, tolerating a calendar outage.

        A planner that refuses to run because Google is slow is worse than one
        that plans against an empty week and says so, so failures here degrade
        to "no known commitments" rather than a 502.
        """
        provider = None
        try:
            provider = runtime.provider(user_id)
            now = local_now()
            result = provider.list_events(250, now, now + timedelta(days=days + 1))
            return result.get('events', []) if result.get('success') else []
        except Exception:
            return []
        finally:
            if provider is not None:
                provider.close()

    @app.get('/study/plan')
    def study_plan(user_id=Depends(current_user)):
        return {'sessions': store.study_sessions(user_id)}

    @app.post('/study/plan')
    def generate_study_plan(days: int = Query(7, ge=1, le=28), user_id=Depends(current_user)):
        work = store.assignments(user_id, include_done=False)
        done_minutes = store.completed_minutes(user_id)
        sessions = plan_sessions(
            work, _calendar_events(user_id, days), store.preferences(user_id),
            days=days, completed_minutes=done_minutes,
        )
        saved = store.replace_study_plan(user_id, sessions)
        return {
            'sessions': saved,
            'unscheduled': unschedulable(work, sessions, completed_minutes=done_minutes),
        }

    @app.put('/study/sessions/{session_id}')
    def update_study_session(session_id: str, status: str = Query(...), user_id=Depends(current_user)):
        try:
            return store.set_session_status(user_id, session_id, status)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except PermissionError:
            raise HTTPException(404, 'Study session not found') from None

    @app.get('/export/calendar.ics')
    def export_ics(days: int = Query(60, ge=1, le=365), user_id=Depends(current_user)):
        body = to_ics(
            _calendar_events(user_id, days),
            store.assignments(user_id),
            store.study_sessions(user_id),
        )
        return Response(
            content=body, media_type='text/calendar; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename="task-pilot.ics"'},
        )

    @app.get('/export/schedule.csv')
    def export_csv(days: int = Query(60, ge=1, le=365), user_id=Depends(current_user)):
        body = to_csv(
            _calendar_events(user_id, days),
            store.assignments(user_id),
            store.study_sessions(user_id),
        )
        return Response(
            content=body, media_type='text/csv; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename="task-pilot-schedule.csv"'},
        )

    @app.get('/study/progress')
    def study_progress(user_id=Depends(current_user)):
        return progress(store.assignments(user_id), store.study_sessions(user_id))

    @app.get('/events', response_model=EventsResponse)
    def events(max_results: int = Query(10,ge=1,le=250), time_min: str|None=None, time_max: str|None=None, user_id=Depends(current_user)):
        try:
            start = coerce_datetime(time_min) if time_min else None
            end = coerce_datetime(time_max) if time_max else None
            if start and end and end<=start:
                raise ValueError('time_max must be after time_min')
        except ValueError:
            raise HTTPException(422, 'Invalid calendar time range') from None
        provider = runtime.provider(user_id)
        try:
            result = provider.list_events(max_results, start, end)
            if not result.get('success'):
                # Name the calendar that failed. Reporting a Google problem
                # while the user is on the native calendar is worse than
                # useless, and a failure is never answered with the other
                # calendar's events.
                raise HTTPException(502, f'Could not read your {provider.label}. {_reconnect_hint(provider)}')
            return result
        finally:
            provider.close()
    @app.post('/internal/reminders', include_in_schema=False)
    def scheduled_reminders(request: Request):
        secret = os.getenv('REMINDER_TRIGGER_SECRET', '')
        if len(secret) < 32:
            raise HTTPException(503, 'Scheduled reminders are not configured.')
        supplied = request.headers.get('authorization', '')
        if not secrets.compare_digest(supplied, 'Bearer ' + secret):
            raise HTTPException(401, 'Invalid scheduler credentials.')
        from app.reminder_worker import calendar_reader
        mailer = create_mailer()
        if not mailer.configured:
            raise HTTPException(503, 'Email delivery is not configured.')
        # Lock and budget span hosts; overlapping or repeated scheduler calls
        # cannot duplicate delivery or flood Google with calendar reads.
        with store.lock('reminders', 'sweep'):
            wait = store.consume_limits([('reminder:sweep', 1, 600)])
            if wait:
                return {'sent': 0, 'failed': 0, 'skipped_accounts': 0, 'already_checked': True}
            return ReminderService(store, mailer, calendar_reader(runtime)).sweep()

    return app
