"""Optional atomic copy of local encrypted data into an empty PostgreSQL DB.

Stop the local app first and back up its data directory. This command never
modifies SQLite and never overwrites an existing PostgreSQL record.
"""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from app.postgres_store import PostgresUserStore

TABLES = ('users', 'sessions', 'oauth', 'conversations', 'preferences', 'assignments',
          'study_sessions', 'reminders_sent', 'usage_counters')
ENCRYPTED = {'users': ('profile', 'tokens'), 'oauth': ('payload',),
             'conversations': ('title', 'state'), 'preferences': ('payload',),
             'assignments': ('payload',), 'study_sessions': ('payload',)}


def migrate(source, destination):
    source = Path(source).resolve()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        src.row_factory = sqlite3.Row
        src.execute('BEGIN')
        existing = {r['name'] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        count = 0
        with destination.lock('migration', 'import'):
            with destination.db() as target:
                if any(target.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone() for table in TABLES):
                    raise ValueError('Migration requires an empty destination; existing data was preserved.')
                for table in TABLES:
                    if table not in existing:
                        continue
                    for row in src.execute('SELECT * FROM ' + table):
                        # Abort the whole transaction if the key cannot decrypt
                        # any protected value. No ciphertext is silently lost.
                        for column in ENCRYPTED.get(table, ()):
                            if column in row.keys() and row[column]:
                                destination.open(row[column])
                        columns = ','.join(row.keys())
                        placeholders = ','.join('?' for _ in row.keys())
                        target.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(row))
                        count += 1
        return count


def main():
    from app.config import PROJECT_ROOT  # Load .env for operator settings.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=str(PROJECT_ROOT / 'data' / 'users.sqlite3'))
    args = parser.parse_args()
    store = PostgresUserStore(os.getenv('DATABASE_URL', ''), os.getenv('TOKEN_ENCRYPTION_KEY'))
    print(f'Migrated {migrate(args.source, store)} encrypted records. Source data was preserved.')


if __name__ == '__main__':
    main()
