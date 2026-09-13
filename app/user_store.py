"""Encrypted user data and durable conversations for a single application host."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet
from filelock import FileLock


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class UserStore:
    def __init__(self, directory, key=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if not key:
            keyfile = self.directory / 'master.key'
            with FileLock(str(self.directory / 'key.lock')):
                if not keyfile.exists():
                    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(Fernet.generate_key())
                key = keyfile.read_bytes()
        self.cipher = Fernet(key)
        self.path = self.directory / 'users.sqlite3'
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, profile TEXT NOT NULL, tokens TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS oauth (id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, state TEXT, busy INTEGER DEFAULT 0, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(user_id, updated);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def open(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))

    def lock(self, kind, identity):
        # Stable across processes; no shared lock is held during model calls.
        return FileLock(str(self.directory / (digest(kind + ':' + identity) + '.lock')), timeout=120)

    def save_user(self, user_id, profile, tokens):
        with self.db() as db:
            db.execute('INSERT INTO users VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET profile=excluded.profile,tokens=excluded.tokens',
                       (user_id, self.seal(profile), self.seal(tokens)))

    def user(self, user_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not row:
            raise KeyError('User not found')
        return self.open(row['profile']), self.open(row['tokens'])

    def session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), user_id, time.time() + 604800))
        return token

    def session_user(self, token):
        with self.db() as db:
            row = db.execute('SELECT user_id FROM sessions WHERE id=? AND expires>?', (digest(token), time.time())).fetchone()
        return row['user_id'] if row else None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (digest(token),))

    def oauth_start(self, payload):
        state = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM oauth WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO oauth VALUES (?,?,?)', (digest(state), self.seal(payload), time.time()+600))
        return state

    def oauth_finish(self, state):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM oauth WHERE id=? AND expires>?', (digest(state), time.time())).fetchone()
            db.execute('DELETE FROM oauth WHERE id=?', (digest(state),))
        return self.open(row['payload']) if row else None

    def conversation(self, user_id, thread_id, create=False):
        with self.db() as db:
            if create:
                db.execute('INSERT OR IGNORE INTO conversations(id,user_id,updated) VALUES (?,?,?)', (thread_id,user_id,time.time()))
            row = db.execute('SELECT * FROM conversations WHERE id=? AND user_id=?', (thread_id,user_id)).fetchone()
        if not row:
            raise PermissionError('Conversation not found')
        return self.open(row['state']) if row['state'] else None, bool(row['busy'])

    def save_conversation(self, user_id, thread_id, state=None, busy=False):
        with self.db() as db:
            if state is None:
                db.execute('UPDATE conversations SET busy=? WHERE id=? AND user_id=?', (int(busy),thread_id,user_id))
            else:
                db.execute('UPDATE conversations SET state=?,busy=?,updated=? WHERE id=? AND user_id=?', (self.seal(state),int(busy),time.time(),thread_id,user_id))

    def conversations(self, user_id):
        with self.db() as db:
            rows = db.execute('SELECT id,updated FROM conversations WHERE user_id=? ORDER BY updated DESC LIMIT 100', (user_id,)).fetchall()
        return [dict(row) for row in rows]
