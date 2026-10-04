"""Serve the built React app and authenticated API from one HTTPS origin."""
from pathlib import Path
from urllib.parse import urlparse
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.config import PROJECT_ROOT


def create_web_app(api_app=None, frontend_directory=None):
    directory = Path(frontend_directory or PROJECT_ROOT / 'frontend' / 'dist')
    if not (directory / 'index.html').is_file():
        raise ValueError('Built frontend is missing. Run npm ci and npm run build in frontend before starting the production server.')
    if api_app is None:
        from app.api import app as api_app
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    secure = urlparse(os.getenv('PUBLIC_APP_URL', '')).scheme == 'https'

    @application.middleware('http')
    async def browser_headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        # Only hashed build assets are cacheable. Never cache OAuth or user data.
        response.headers['Cache-Control'] = ('public, max-age=31536000, immutable'
            if request.url.path.startswith('/assets/') and response.status_code == 200 else 'no-store')
        return response

    application.mount('/api', api_app)
    application.mount('/', StaticFiles(directory=str(directory), html=True), name='frontend')
    return application
