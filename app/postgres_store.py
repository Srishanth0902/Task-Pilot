"""PostgreSQL persistence with the same encrypted records as local SQLite."""
import hashlib
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet

from app.user_store import UserStore


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, profile TEXT NOT NULL, tokens TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS oauth (id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT, state TEXT, busy INTEGER DEFAULT 0, updated DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(user_id, updated);
CREATE TABLE IF NOT EXISTS preferences (user_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignments (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT NOT NULL, due DOUBLE PRECISION NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS assignment_owner ON assignments(user_id, due);
CREATE TABLE IF NOT EXISTS study_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, assignment_id TEXT, payload TEXT NOT NULL, start DOUBLE PRECISION NOT NULL, finish DOUBLE PRECISION NOT NULL, minutes INTEGER NOT NULL, status TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS study_owner ON study_sessions(user_id, start);
CREATE TABLE IF NOT EXISTS reminders_sent (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, sent DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS reminder_age ON reminders_sent(sent);
CREATE TABLE IF NOT EXISTS usage_counters (id TEXT PRIMARY KEY, used INTEGER NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS native_events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT NOT NULL, start DOUBLE PRECISION NOT NULL, finish DOUBLE PRECISION NOT NULL, all_day INTEGER DEFAULT 0, status TEXT NOT NULL, version INTEGER NOT NULL, created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS native_event_owner ON native_events(user_id, start);
'''


class Queries:
    """Adapt the store's internal, parameterized SQL to PostgreSQL.

    This is only for application-owned queries, never user-provided SQL.
    Values remain bound parameters; no values are interpolated into SQL.
    """
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, values=()):
        ignored_insert = 'INSERT OR IGNORE INTO' in statement
        statement = statement.replace('INSERT OR IGNORE INTO', 'INSERT INTO')
        if ignored_insert:
            statement += ' ON CONFLICT DO NOTHING'
        return self.connection.execute(statement.replace('?', '%s'), values)


class PostgresUserStore(UserStore):
    def __init__(self, url, key):
        if not key:
            raise ValueError('PostgreSQL requires an external TOKEN_ENCRYPTION_KEY.')
        parsed = urlparse(url)
        if parsed.scheme not in {'postgres', 'postgresql'} or not parsed.hostname:
            raise ValueError('DATABASE_URL must be a PostgreSQL connection URL.')
        if parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            if parse_qs(parsed.query).get('sslmode', [''])[0] not in {'require', 'verify-ca', 'verify-full'}:
                raise ValueError('Remote DATABASE_URL requires sslmode=require or stronger.')
        self.url = url
        self.cipher = Fernet(key)
        # Kept for the existing interface; PostgreSQL never creates local keys,
        # data files, or locks, and never falls back to SQLite on a DB outage.
        self.directory = Path('.')
        self.path = None
        with self.lock('schema', 'initialize'):
            with self.connection() as db:
                for statement in SCHEMA.split(';'):
                    if statement.strip():
                        db.execute(statement)

    def connection(self):
        return psycopg.connect(self.url, connect_timeout=10, row_factory=dict_row)

    @contextmanager
    def db(self):
        with self.connection() as db:
            yield Queries(db)

    @contextmanager
    def lock(self, kind, identity, timeout=120):
        # Transaction locks work with Neon's pooled URLs and release on a crash.
        # A separate transaction holds the lock while store methods use their
        # own short transactions. No global lock surrounds calendar/LLM calls.
        lock_id = int.from_bytes(hashlib.sha256((kind + ':' + identity).encode()).digest()[:8],
                                 'big', signed=True)
        deadline = time.monotonic() + timeout
        with self.connection() as db:
            db.execute("SET LOCAL idle_in_transaction_session_timeout = '0'")
            while not db.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired', (lock_id,)).fetchone()['acquired']:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another request is still processing. Try again shortly.')
                time.sleep(0.05)
            yield

    def oauth_finish(self, state):
        from app.user_store import digest
        with self.db() as db:
            row = db.execute('DELETE FROM oauth WHERE id=? AND expires>? RETURNING payload',
                             (digest(state), time.time())).fetchone()
        return self.open(row['payload']) if row else None
