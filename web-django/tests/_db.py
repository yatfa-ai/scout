"""Scratch-database lifecycle for the DB-backed tests.

Most of this project's invariants are settings or metadata, so `tests/_probe.py`
opens no database at all. The approval queue is the exception: it is nothing
but database behaviour, and neither of the two obvious routes can host it —
`TEST_RUNNER = 'socweb.test_runner.NoDatabaseTestRunner'` creates no test
database, and even the stock runner's would be empty of social tables, because
every model is `managed = False` and `social/migrations/` holds only
`__init__.py`.

So the harness builds its own database from the repo-root `schema.sql`, exactly
as the Node side does in `test/web-approval-gate.test.mjs`: probe a DSN, create
a scratch database, load the schema, and drop the database afterwards. With no
reachable Postgres `scratch_database()` returns None and the caller skips
honestly, so the suite stays green by default.

⚠️ Two encoding details are load-bearing, both halves needed: `schema.sql`
carries Cyrillic comments, and executing it through psycopg against a
`SQL_ASCII` server raises `UnicodeEncodeError`. The scratch database is
therefore created `encoding 'UTF8' template template0`, and every connection
sets `client_encoding='utf8'`. The `psql -f` path never shows this, so a
successful check by hand does not clear the Python harness.
"""

import os
from pathlib import Path

import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row

# web-django/
PROJECT_DIR = Path(__file__).resolve().parent.parent
# Repo root: `schema.sql` there is the schema source of truth, and the harness
# only ever reads it.
REPO_ROOT = PROJECT_DIR.parent
SCHEMA_SQL = REPO_ROOT / 'schema.sql'

# Same search order as `test/web-approval-gate.test.mjs`: an explicit
# TEST_DATABASE_URL wins, then the usual local ones.
CANDIDATE_DSN = [
    dsn
    for dsn in (
        os.environ.get('TEST_DATABASE_URL'),
        'postgres://postgres:postgres@127.0.0.1:5432/postgres',
        'postgres://postgres@127.0.0.1:5432/postgres',
        'postgres:///postgres',  # socket + peer authentication as the current user
    )
    if dsn
]

# Shown by a skipped example, so a green run never looks like a passing one.
NO_DATABASE = 'no live local Postgres (set TEST_DATABASE_URL) — queue transition checks skipped'

# A short timeout keeps the no-database path fast instead of hanging the suite.
CONNECT_TIMEOUT = 3


def connect(dsn, **kwargs):
    """An autocommit connection with dict rows and UTF-8 on the wire."""
    return psycopg.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=CONNECT_TIMEOUT,
        client_encoding='utf8',
        **kwargs,
    )


def probe_dsn():
    """The first candidate DSN that answers, or None when none does."""
    for dsn in CANDIDATE_DSN:
        try:
            with connect(dsn):
                return dsn
        except psycopg.Error:
            continue  # try the next one
    return None


def swap_database(dsn, name):
    """`dsn` with its database replaced by `name`, keeping any query string.

    Written by hand rather than with a URL parser: invariant (d) — pinned by
    `tests/test_no_http_client.py` — forbids importing `urllib` anywhere under
    `web-django/`, because nothing in this project may reach the network.
    """
    scheme, separator, tail = dsn.partition('://')
    if not separator:
        raise ValueError(f'a DSN must be a URL, got {dsn!r}')
    authority, _, path = tail.partition('/')
    _, question, query = path.partition('?')
    return f'{scheme}://{authority}/{name}{question}{query}'


class ScratchDatabase:
    """A database built from `schema.sql`, dropped when the caller is done."""

    def __init__(self, admin_dsn, name):
        self.admin_dsn = admin_dsn
        self.name = name
        self.url = swap_database(admin_dsn, name)

    def create(self):
        """Drop any leftover database of this name, create it, load the schema."""
        with connect(self.admin_dsn) as admin:
            self._drop_with(admin)
            admin.execute(
                pgsql.SQL("create database {} encoding 'UTF8' template template0").format(
                    pgsql.Identifier(self.name)
                )
            )
        # psycopg sends a parameterless execute as a simple query, so the whole
        # file goes in one statement, like `psql -f schema.sql`.
        with connect(self.url) as db:
            db.execute(SCHEMA_SQL.read_text(encoding='utf-8'))

    def drop(self):
        with connect(self.admin_dsn) as admin:
            self._drop_with(admin)

    def _drop_with(self, admin):
        admin.execute(
            pgsql.SQL('drop database if exists {} with (force)').format(
                pgsql.Identifier(self.name)
            )
        )

    def sql(self, text, params=()):
        """Run one statement against the scratch database; return its rows."""
        with connect(self.url) as db:
            cursor = db.execute(text, params)
            return cursor.fetchall() if cursor.description else []

    def tables(self):
        rows = self.sql(
            "select tablename from pg_tables where schemaname = 'public' order by tablename"
        )
        return [row['tablename'] for row in rows]


def scratch_database(name):
    """A loaded ScratchDatabase, or None when no Postgres is reachable."""
    admin_dsn = probe_dsn()
    if admin_dsn is None:
        return None
    database = ScratchDatabase(admin_dsn, name)
    database.create()
    return database
