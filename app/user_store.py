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
                CREATE TABLE IF NOT EXISTS preferences (user_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assignments (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT NOT NULL, due REAL NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS assignment_owner ON assignments(user_id, due);
                CREATE TABLE IF NOT EXISTS study_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, assignment_id TEXT, payload TEXT NOT NULL, start REAL NOT NULL, finish REAL NOT NULL, minutes INTEGER NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS study_owner ON study_sessions(user_id, start);
                CREATE TABLE IF NOT EXISTS reminders_sent (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, sent REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS reminder_age ON reminders_sent(sent);
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

    def preferences(self, user_id):
        from app.preferences import SchedulingPreferences
        with self.db() as db:
            row = db.execute('SELECT payload FROM preferences WHERE user_id=?', (user_id,)).fetchone()
        return SchedulingPreferences.model_validate(self.open(row['payload']) if row else {}).model_dump()

    def save_preferences(self, user_id, preferences):
        from app.preferences import SchedulingPreferences
        validated = SchedulingPreferences.model_validate(preferences).model_dump()
        with self.db() as db:
            db.execute('INSERT INTO preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload', (user_id, self.seal(validated)))
        return validated

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

    # ---- assignments ---------------------------------------------------
    # Titles, subjects and notes are coursework details, so they are sealed
    # like everything else. Deadline, priority and status stay in plain
    # columns because the reminder sweep and the planner have to query on
    # them, and on their own they reveal nothing about what the work is.

    def save_assignment(self, user_id, assignment, assignment_id=None):
        """Insert or replace one assignment; returns the stored record."""
        from app.assignments import Assignment

        validated = Assignment.model_validate(assignment)
        now = time.time()
        identity = assignment_id or secrets.token_urlsafe(12)
        payload = {
            'title': validated.title,
            'subject': validated.subject,
            'notes': validated.notes,
            'estimated_minutes': validated.estimated_minutes,
        }
        with self.db() as db:
            if assignment_id:
                owned = db.execute(
                    'SELECT created FROM assignments WHERE id=? AND user_id=?',
                    (assignment_id, user_id),
                ).fetchone()
                if not owned:
                    raise PermissionError('Assignment not found')
                created = owned['created']
            else:
                created = now
            db.execute(
                'INSERT INTO assignments VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                'payload=excluded.payload,due=excluded.due,priority=excluded.priority,'
                'status=excluded.status,updated=excluded.updated',
                (identity, user_id, self.seal(payload), validated.due.timestamp(),
                 validated.priority, validated.status, created, now),
            )
        return self.assignment(user_id, identity)

    def _assignment_record(self, row):
        from datetime import datetime, timezone

        payload = self.open(row['payload'])
        return {
            'id': row['id'],
            'title': payload.get('title', ''),
            'subject': payload.get('subject', ''),
            'notes': payload.get('notes', ''),
            'estimated_minutes': payload.get('estimated_minutes', 60),
            'due': datetime.fromtimestamp(row['due'], timezone.utc).isoformat(),
            'due_timestamp': row['due'],
            'priority': row['priority'],
            'status': row['status'],
            'created': row['created'],
            'updated': row['updated'],
        }

    def assignment(self, user_id, assignment_id):
        with self.db() as db:
            row = db.execute(
                'SELECT * FROM assignments WHERE id=? AND user_id=?',
                (assignment_id, user_id),
            ).fetchone()
        if not row:
            raise PermissionError('Assignment not found')
        return self._assignment_record(row)

    def assignments(self, user_id, include_done=True):
        query = 'SELECT * FROM assignments WHERE user_id=?'
        if not include_done:
            query += " AND status!='done'"
        query += ' ORDER BY due LIMIT 500'
        with self.db() as db:
            rows = db.execute(query, (user_id,)).fetchall()
        return [self._assignment_record(row) for row in rows]

    def delete_assignment(self, user_id, assignment_id):
        with self.db() as db:
            cursor = db.execute(
                'DELETE FROM assignments WHERE id=? AND user_id=?',
                (assignment_id, user_id),
            )
        return cursor.rowcount == 1

    def assignments_due_between(self, start, end):
        """Unfinished assignments across all accounts, for the reminder sweep."""
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM assignments WHERE status!='done' AND due>=? AND due<=? ORDER BY due",
                (start, end),
            ).fetchall()
        return [(row['user_id'], self._assignment_record(row)) for row in rows]

    def user_ids(self):
        with self.db() as db:
            return [row['id'] for row in db.execute('SELECT id FROM users')]

    # ---- reminder de-duplication ---------------------------------------

    def reminder_claim(self, user_id, key, retention=30 * 24 * 60 * 60):
        """Claim one reminder exactly once; False means it already went out.

        The insert itself is the claim, so two sweeps racing on the same
        reminder cannot both win and double-send.
        """
        now = time.time()
        with self.db() as db:
            db.execute('DELETE FROM reminders_sent WHERE sent<?', (now - retention,))
            cursor = db.execute(
                'INSERT OR IGNORE INTO reminders_sent VALUES (?,?,?)',
                (digest(user_id + ':' + key), user_id, now),
            )
        return cursor.rowcount == 1

    # ---- study sessions -------------------------------------------------

    def _session_record(self, row):
        from datetime import datetime, timezone

        payload = self.open(row['payload'])
        return {
            'id': row['id'],
            'assignment_id': row['assignment_id'],
            'title': payload.get('title', ''),
            'subject': payload.get('subject', 'General'),
            'start': datetime.fromtimestamp(row['start'], timezone.utc).isoformat(),
            'end': datetime.fromtimestamp(row['finish'], timezone.utc).isoformat(),
            'minutes': row['minutes'],
            'status': row['status'],
        }

    def replace_study_plan(self, user_id, sessions):
        """Store a freshly generated plan.

        Only sessions still marked 'planned' are discarded: a session the
        student already ticked off is history, and re-planning must not erase
        the record of work actually done.
        """
        from datetime import datetime

        now = time.time()
        with self.db() as db:
            db.execute("DELETE FROM study_sessions WHERE user_id=? AND status='planned'", (user_id,))
            for session in sessions:
                payload = {'title': session.get('title', ''), 'subject': session.get('subject', 'General')}
                db.execute(
                    'INSERT INTO study_sessions VALUES (?,?,?,?,?,?,?,?,?)',
                    (secrets.token_urlsafe(12), user_id, session.get('assignment_id'),
                     self.seal(payload),
                     datetime.fromisoformat(session['start']).timestamp(),
                     datetime.fromisoformat(session['end']).timestamp(),
                     int(session.get('minutes', 0)), 'planned', now),
                )
        return self.study_sessions(user_id)

    def study_sessions(self, user_id):
        with self.db() as db:
            rows = db.execute(
                'SELECT * FROM study_sessions WHERE user_id=? ORDER BY start LIMIT 500',
                (user_id,),
            ).fetchall()
        return [self._session_record(row) for row in rows]

    def set_session_status(self, user_id, session_id, status):
        if status not in {'planned', 'done', 'skipped'}:
            raise ValueError('Study session status must be planned, done, or skipped')
        with self.db() as db:
            cursor = db.execute(
                'UPDATE study_sessions SET status=? WHERE id=? AND user_id=?',
                (status, session_id, user_id),
            )
        if cursor.rowcount != 1:
            raise PermissionError('Study session not found')
        return self.study_session(user_id, session_id)

    def study_session(self, user_id, session_id):
        with self.db() as db:
            row = db.execute(
                'SELECT * FROM study_sessions WHERE id=? AND user_id=?',
                (session_id, user_id),
            ).fetchone()
        if not row:
            raise PermissionError('Study session not found')
        return self._session_record(row)

    def completed_minutes(self, user_id):
        """Minutes already studied per assignment, for re-planning."""
        with self.db() as db:
            rows = db.execute(
                "SELECT assignment_id, SUM(minutes) AS total FROM study_sessions "
                "WHERE user_id=? AND status='done' GROUP BY assignment_id",
                (user_id,),
            ).fetchall()
        return {row['assignment_id']: row['total'] for row in rows if row['assignment_id']}
