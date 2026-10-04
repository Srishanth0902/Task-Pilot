"""Run against real PostgreSQL in CI; never contacts a user's Neon account."""
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
import psycopg
from psycopg import sql
from cryptography.fernet import Fernet
from app.postgres_store import PostgresUserStore
from app.migrate_storage import migrate
from app.user_store import UserStore
from tests import test_multiuser
from tests import test_free_hosting


class PostgresFixture:
    def setUp(self):
        url = os.environ['TEST_DATABASE_URL']
        self.schema = 'test_' + uuid.uuid4().hex
        with psycopg.connect(url) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        # Use a URL so the production URL validation is also exercised.
        from urllib.parse import quote
        self.pg_url = url + ('&' if '?' in url else '?') + 'options=' + quote('-c search_path=' + self.schema)
        self.addCleanup(self.drop_schema)
        self.factory_patch = patch('tests.test_multiuser.UserStore',
            side_effect=lambda directory, key: PostgresUserStore(self.pg_url, key))
        self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        super().setUp()

    def drop_schema(self):
        with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Real PostgreSQL is required; supplied by CI service')
class PostgresMultiuserTests(PostgresFixture, test_multiuser.MultiuserTests):
    def test_assignment_study_and_reminder_storage_preserve_time_and_ownership(self):
        from datetime import datetime, timedelta
        start = datetime.fromisoformat('2026-10-05T18:01:23.123456+05:30')
        work = self.store.save_assignment('alice', {'title': 'Private assignment', 'due': start.isoformat()})
        self.assertEqual(datetime.fromisoformat(work['due']).timestamp(), start.timestamp())
        with self.assertRaises(PermissionError):
            self.store.assignment('bob', work['id'])
        sessions = self.store.replace_study_plan('alice', [{'assignment_id': work['id'],
            'title': 'Private study', 'start': start.isoformat(),
            'end': (start + timedelta(minutes=45)).isoformat(), 'minutes': 45}])
        self.assertEqual(datetime.fromisoformat(sessions[0]['start']).timestamp(), start.timestamp())
        self.assertEqual(self.store.study_sessions('bob'), [])
        self.store.set_session_status('alice', sessions[0]['id'], 'done')
        self.assertEqual(self.store.completed_minutes('alice'), {work['id']: 45})
        self.assertTrue(self.store.reminder_claim('alice', 'lead'))
        self.assertFalse(self.store.reminder_claim('alice', 'lead'))
        self.assertTrue(self.store.reminder_claim('bob', 'lead'))

    def test_ciphertext_and_empty_destination_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            source = UserStore(folder, self.key)
            source.save_user('new', {'email': 'private@example.com'}, {'refresh_token': 'sensitive'})
            with self.assertRaisesRegex(ValueError, 'empty'):
                migrate(source.path, self.store)
            with self.store.db() as db:
                rows = db.execute('SELECT profile,tokens FROM users').fetchall()
            self.assertNotIn('private-alice', str(rows))
            self.assertNotIn('alice@example.com', str(rows))

    def test_migration_preserves_data_and_rejects_wrong_key(self):
        # A separate schema makes the migration target empty.
        with self.store.db() as db:
            for table in ('users', 'sessions', 'conversations', 'preferences'):
                db.execute('DELETE FROM ' + table)
        with tempfile.TemporaryDirectory() as folder:
            source = UserStore(folder, self.key)
            source.save_user('migrated', {'email': 'secret@example.com'}, {'refresh_token': 'sensitive'})
            token = source.session('migrated')
            wrong = PostgresUserStore(self.pg_url, Fernet.generate_key())
            from cryptography.fernet import InvalidToken
            with self.assertRaises(InvalidToken):
                migrate(source.path, wrong)
            with self.store.db() as db:
                self.assertIsNone(db.execute('SELECT 1 FROM users').fetchone())
            self.assertEqual(migrate(source.path, self.store), 2)
            self.assertEqual(self.store.user('migrated')[1]['refresh_token'], 'sensitive')
            self.assertEqual(self.store.session_user(token), 'migrated')


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Real PostgreSQL is required; supplied by CI service')
class PostgresBudgetTests(PostgresFixture, test_free_hosting.FreeHostingTests):
    def setUp(self):
        # FreeHostingTests uses its imported UserStore, so patch only its factory.
        self.pg_patch = patch('tests.test_free_hosting.UserStore',
            side_effect=lambda directory, key: PostgresUserStore(self.pg_url, key))
        # Start after PostgresFixture creates the schema, before parent setup.
        super().setUp()
        self.store = PostgresUserStore(self.pg_url, self.key)
        self.store.save_user('alice', {'email': 'alice@example.com'}, {'refresh_token': 'private'})
        self.pg_patch.start()
        self.addCleanup(self.pg_patch.stop)
