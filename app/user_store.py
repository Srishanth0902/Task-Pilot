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
                CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT, state TEXT, busy INTEGER DEFAULT 0, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(user_id, updated);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(conversations)')}
            if 'title' not in columns:
                db.execute('ALTER TABLE conversations ADD COLUMN title TEXT')

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

    def session(self, user_id, max_age=30 * 24 * 60 * 60):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), user_id, time.time() + max_age))
        return token

    def session_user(self, token):
        with self.db() as db:
            row = db.execute('SELECT user_id FROM sessions WHERE id=? AND expires>?', (digest(token), time.time())).fetchone()
        return row['user_id'] if row else None

    def touch_session(self, token, max_age=30 * 24 * 60 * 60):
        """Renew an active session without rotating or exposing its secret."""
        now = time.time()
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            cursor = db.execute(
                'UPDATE sessions SET expires=? WHERE id=?',
                (now + max_age, digest(token)),
            )
        return cursor.rowcount == 1

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

    def name_conversation(self, user_id, thread_id, message):
        """Set a private, stable title from the first meaningful user message."""
        title = ' '.join(str(message).split()).strip(' \t\r\n"\'')
        if not title:
            title = 'New conversation'
        if len(title) > 58:
            title = title[:57].rstrip(' ,.;:-') + '…'
        with self.db() as db:
            db.execute(
                'UPDATE conversations SET title=? WHERE id=? AND user_id=? AND title IS NULL',
                (self.seal(title), thread_id, user_id),
            )
        return title

    def conversations(self, user_id):
        with self.db() as db:
            rows = db.execute('SELECT id,title,state,updated FROM conversations WHERE user_id=? ORDER BY updated DESC LIMIT 100', (user_id,)).fetchall()
        result = []
        for row in rows:
            title = self.open(row['title']) if row['title'] else None
            # Give conversations saved by earlier versions a useful title the
            # first time they are listed, without exposing it in plaintext.
            if not title and row['state']:
                state = self.open(row['state'])
                first = next(
                    (
                        message.get('data', {}).get('content')
                        for message in state.get('messages', [])
                        if message.get('type') == 'human'
                    ),
                    None,
                )
                if first:
                    title = self.name_conversation(user_id, row['id'], first)
            result.append({'id': row['id'], 'title': title or 'Conversation', 'updated': row['updated']})
        return result
