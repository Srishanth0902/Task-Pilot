"""Authenticated HTTP application; no shared Desktop OAuth token fallback."""
import json
import os
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from langchain_core.messages import messages_from_dict, messages_to_dict

from app.config import PROJECT_ROOT, OPENROUTER_MODEL, TIMEZONE, OPENROUTER_API_KEY
from app.calendar_service import get_events
from app.date_utils import coerce_datetime
from app.graph_agent import CalendarConversation
from app.llm import create_openrouter_model
from app.user_store import UserStore

COOKIE = 'task_pilot_session'
SCOPES = ['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile', 'https://www.googleapis.com/auth/calendar']


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

    def chat(self, user_id, thread_id, message):
        with self.store.lock('conversation', thread_id):
            saved, interrupted = self.store.conversation(user_id, thread_id, create=True)
            if interrupted:
                raise HTTPException(409, 'The previous request was interrupted. Check your calendar and start a new conversation before making more changes.')
            service = self.service(user_id)
            try:
                chat = CalendarConversation(self.model_factory(), service)
                if saved:
                    saved['messages'] = messages_from_dict(saved.get('messages', []))
                    chat.graph.update_state({'configurable': {'thread_id': thread_id}}, saved)
                # A crash must never lead to automatically replaying a calendar write.
                self.store.save_conversation(user_id, thread_id, busy=True)
                result = chat.ask(message, thread_id=thread_id)
                serial = dict(result, messages=messages_to_dict(result.get('messages', [])))
                self.store.save_conversation(user_id, thread_id, serial)
                return result
            finally:
                if hasattr(service, 'close'):
                    service.close()


def create_multiuser_app(store=None, runtime=None, *, origin=None, oauth_file=None):
    # Imported here to reuse the established response contract without a cycle.
    from app.api import ChatRequest, ChatResponse, EventsResponse, _chat_response
    origin = (origin or os.getenv('PUBLIC_APP_URL', 'http://127.0.0.1:5173')).rstrip('/')
    secure = urlparse(origin).scheme == 'https'
    if not secure and urlparse(origin).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('PUBLIC_APP_URL must use HTTPS outside localhost')
    key = os.getenv('TOKEN_ENCRYPTION_KEY')
    if secure and not key and store is None:
        raise ValueError('TOKEN_ENCRYPTION_KEY is required for deployment')
    store = store or UserStore(os.getenv('DATA_DIRECTORY', str(PROJECT_ROOT / 'data')), key)
    runtime = runtime or UserRuntime(store)
    oauth_file = Path(oauth_file or os.getenv('GOOGLE_WEB_CREDENTIALS_FILE', str(PROJECT_ROOT / 'credentials.web.json')))
    redirect_uri = origin + '/api/auth/callback'
    app = FastAPI(title='Task Pilot', version='2.0.0')
    app.state.store, app.state.runtime = store, runtime

    @app.middleware('http')
    async def private_headers(request, call_next):
        response = await call_next(request)
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
                'google_token_configured':False, 'authentication_required':True}

    @app.get('/auth/me')
    def me(request: Request):
        user_id = store.session_user(request.cookies.get(COOKIE, ''))
        return {'authenticated':bool(user_id), 'user':store.user(user_id)[0] if user_id else None,
                'login_configured':oauth_file.exists()}

    @app.get('/auth/login')
    def login():
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        oauth = flow(code_verifier=verifier)
        state = store.oauth_start({'nonce':nonce, 'verifier':verifier})
        url, _ = oauth.authorization_url(state=state, nonce=nonce, access_type='offline', prompt='consent', code_challenge_method='S256')
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
            if 'https://www.googleapis.com/auth/calendar' not in granted:
                raise ValueError('Calendar access not granted')
            claims = id_token.verify_oauth2_token(oauth.credentials.id_token, GoogleRequest(), oauth.client_config['client_id'])
            if claims.get('nonce') != pending['nonce'] or not claims.get('email_verified') or not claims.get('sub'):
                raise ValueError('Invalid identity')
            user_id = claims['sub']
            with store.lock('tokens', user_id):
                tokens = json.loads(oauth.credentials.to_json())
                if not tokens.get('refresh_token'):
                    try:
                        tokens['refresh_token'] = store.user(user_id)[1].get('refresh_token')
                    except KeyError:
                        pass
                if not tokens.get('refresh_token'):
                    raise ValueError('Offline access not granted')
                store.save_user(user_id, {'id':user_id, 'email':claims['email'], 'name':claims.get('name',claims['email'])}, tokens)
        except Exception:
            raise HTTPException(400, 'Google sign-in could not be completed. Please grant Calendar access and try again.') from None
        store.logout(request.cookies.get(COOKIE, ''))
        response = RedirectResponse(origin, status_code=303)
        response.set_cookie(COOKIE, store.session(user_id), max_age=604800, httponly=True, secure=secure, samesite='lax', path='/')
        response.delete_cookie('task_pilot_oauth', path='/')
        return response

    @app.post('/auth/logout')
    def logout(request: Request, user_id=Depends(current_user)):
        store.logout(request.cookies.get(COOKIE, ''))
        from fastapi.responses import JSONResponse
        response = JSONResponse({'success':True})
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.get('/conversations')
    def conversations(user_id=Depends(current_user)):
        return store.conversations(user_id)

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
        thread_id = body.thread_id or str(uuid.uuid4())
        try:
            return _chat_response(runtime.chat(user_id,thread_id,body.message),thread_id)
        except PermissionError:
            raise HTTPException(404, 'Conversation not found') from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'The request could not finish. Check your calendar before retrying any changes.') from None

    @app.get('/events', response_model=EventsResponse)
    def events(max_results: int = Query(10,ge=1,le=250), time_min: str|None=None, time_max: str|None=None, user_id=Depends(current_user)):
        try:
            start = coerce_datetime(time_min) if time_min else None
            end = coerce_datetime(time_max) if time_max else None
            if start and end and end<=start:
                raise ValueError('time_max must be after time_min')
        except ValueError:
            raise HTTPException(422, 'Invalid calendar time range') from None
        service = runtime.service(user_id)
        try:
            result = get_events(service,max_results,start,end)
            if not result.get('success'):
                raise HTTPException(502, 'Could not read your Google Calendar. Try reconnecting your account.')
            return result
        finally:
            if hasattr(service,'close'):
                service.close()
    return app
