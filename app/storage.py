"""Select durable cloud storage explicitly, preserving local development."""
import os
from app.config import PROJECT_ROOT
from app.user_store import UserStore


def create_store():
    url = os.getenv('DATABASE_URL', '').strip()
    key = os.getenv('TOKEN_ENCRYPTION_KEY')
    if url:
        from app.postgres_store import PostgresUserStore
        return PostgresUserStore(url, key)
    if os.getenv('DEPLOYMENT_MODE') == 'free':
        raise ValueError('Free deployment requires DATABASE_URL; local SQLite is not durable on Render.')
    return UserStore(os.getenv('DATA_DIRECTORY', str(PROJECT_ROOT / 'data')), key)
