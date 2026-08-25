"""Storage layer, speaking either SQLite or Postgres.

Which one is decided by ``DATABASE_URL``:

* unset            -> SQLite at ``data/consultant_experience.db``. Zero setup,
                      used for local development and the test suite.
* ``postgresql://`` -> Postgres (Supabase in the hosted deployment).

Callers write SQL once, using ``?`` placeholders and the ANSI upsert syntax that
both engines accept. The thin wrapper below translates placeholders and hands
back plain dicts, so nothing above this module cares which engine is live.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import get_settings

# --------------------------------------------------------------------- DDL

_SQLITE_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    filename          TEXT,
    source_type       TEXT NOT NULL,
    consultant        TEXT,
    client            TEXT,
    role              TEXT,
    placement_period  TEXT,
    tags              TEXT NOT NULL DEFAULT '[]',
    notes             TEXT,
    sha256            TEXT UNIQUE,
    object_key        TEXT,
    n_chunks          INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'indexed',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_role ON documents(role);
CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    locator      TEXT,
    heading      TEXT,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id  INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    provider  TEXT NOT NULL,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vector    BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    role_focus  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    citations   TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);

CREATE TABLE IF NOT EXISTS interview_sessions (
    id          TEXT PRIMARY KEY,
    role_focus  TEXT NOT NULL,
    level       TEXT NOT NULL,
    topic       TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS interview_turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    ordinal       INTEGER NOT NULL,
    question      TEXT NOT NULL,
    question_kind TEXT NOT NULL DEFAULT 'main',
    answer        TEXT,
    score         INTEGER,
    feedback      TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_interview_turns_session ON interview_turns(session_id);

CREATE TABLE IF NOT EXISTS kb_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
INSERT INTO kb_meta (key, value) VALUES ('version', '0')
    ON CONFLICT (key) DO NOTHING;
"""

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    filename          TEXT,
    source_type       TEXT NOT NULL,
    consultant        TEXT,
    client            TEXT,
    role              TEXT,
    placement_period  TEXT,
    tags              TEXT NOT NULL DEFAULT '[]',
    notes             TEXT,
    sha256            TEXT UNIQUE,
    object_key        TEXT,
    n_chunks          INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'indexed',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_role ON documents(role);
CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client);

CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    locator      TEXT,
    heading      TEXT,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id  BIGINT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    provider  TEXT NOT NULL,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vector    BYTEA NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    role_focus  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    citations   TEXT NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);

CREATE TABLE IF NOT EXISTS interview_sessions (
    id          TEXT PRIMARY KEY,
    role_focus  TEXT NOT NULL,
    level       TEXT NOT NULL,
    topic       TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interview_turns (
    id            BIGSERIAL PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    ordinal       INTEGER NOT NULL,
    question      TEXT NOT NULL,
    question_kind TEXT NOT NULL DEFAULT 'main',
    answer        TEXT,
    score         INTEGER,
    feedback      TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_interview_turns_session ON interview_turns(session_id);

CREATE TABLE IF NOT EXISTS kb_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
INSERT INTO kb_meta (key, value) VALUES ('version', '0')
    ON CONFLICT (key) DO NOTHING;
"""

# ------------------------------------------------------------------ engine

_init_lock = threading.Lock()
_initialised = False
_pool = None  # psycopg_pool.ConnectionPool, created lazily for Postgres


def is_postgres() -> bool:
    return get_settings().database_url.startswith(("postgres://", "postgresql://"))


def dialect() -> str:
    return "postgres" if is_postgres() else "sqlite"


def _coerce(value: Any) -> Any:
    """Postgres hands back datetimes; the API contract is ISO strings."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, memoryview):
        return bytes(value)
    return value


class _Cursor:
    """Cursor facade returning plain dicts from either driver."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def fetchone(self) -> dict[str, Any] | None:
        row = self._raw.fetchone()
        return None if row is None else {k: _coerce(v) for k, v in dict(row).items()}

    def fetchall(self) -> list[dict[str, Any]]:
        return [{k: _coerce(v) for k, v in dict(r).items()} for r in self._raw.fetchall()]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount


class Connection:
    """Wraps the driver connection and hides the two dialects' differences."""

    def __init__(self, raw: Any, kind: str) -> None:
        self._raw = raw
        self.kind = kind

    def _sql(self, sql: str) -> str:
        # Callers write '?'; psycopg wants '%s'. No literal '%' appears in our SQL.
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Cursor:
        if self.kind == "postgres":
            cur = self._raw.cursor()
            cur.execute(self._sql(sql), tuple(params))
            return _Cursor(cur)
        return _Cursor(self._raw.execute(sql, tuple(params)))

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        rows = [tuple(r) for r in rows]
        if not rows:
            return
        if self.kind == "postgres":
            cur = self._raw.cursor()
            cur.executemany(self._sql(sql), rows)
        else:
            self._raw.executemany(sql, rows)

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """INSERT returning the new integer id. Postgres has no lastrowid."""
        if self.kind == "postgres":
            cur = self._raw.cursor()
            cur.execute(self._sql(f"{sql.rstrip().rstrip(';')} RETURNING id"), tuple(params))
            return int(cur.fetchone()["id"])
        cur = self._raw.execute(sql, tuple(params))
        return int(cur.lastrowid)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


def _sqlite_connect() -> Connection:
    settings = get_settings()
    settings.ensure_dirs()
    raw = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=30.0)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Connection(raw, "sqlite")


def _get_pool():
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        settings = get_settings()
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.db_pool_size,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
    return _pool


#: Columns added after the first release. ``CREATE TABLE IF NOT EXISTS`` leaves an
#: existing table untouched, so a database created by an earlier version needs
#: these added explicitly or every query naming them fails.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("documents", "object_key", "TEXT"),
)


def _existing_columns(conn: Connection, table: str) -> set[str]:
    if conn.kind == "postgres":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table,),
        ).fetchall()
        return {r["column_name"] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _apply_column_migrations(conn: Connection) -> list[str]:
    applied: list[str] = []
    for table, column, coltype in _ADDED_COLUMNS:
        if column not in _existing_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            applied.append(f"{table}.{column}")
    return applied


def init_db() -> None:
    """Create the schema and bring an older database up to date.

    Safe to call repeatedly.
    """
    global _initialised
    import logging

    log = logging.getLogger(__name__)

    with _init_lock:
        if is_postgres():
            pool = _get_pool()
            with pool.connection() as raw:
                raw.execute(_POSTGRES_SCHEMA)
                conn = Connection(raw, "postgres")
                applied = _apply_column_migrations(conn)
                raw.commit()
        else:
            conn = _sqlite_connect()
            try:
                conn._raw.executescript(_SQLITE_SCHEMA)
                applied = _apply_column_migrations(conn)
                conn.commit()
            finally:
                conn.close()

        if applied:
            log.info("Schema brought up to date: added %s", ", ".join(applied))
        _initialised = True


@contextmanager
def connection() -> Iterator[Connection]:
    """Short-lived connection, committed on clean exit."""
    if not _initialised:
        init_db()

    if is_postgres():
        pool = _get_pool()
        with pool.connection() as raw:
            # The pool commits on clean exit and rolls back on exception.
            yield Connection(raw, "postgres")
        return

    conn = _sqlite_connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ------------------------------------------------------------------ helpers

def bump_kb_version(conn: Connection) -> int:
    """Invalidate cached search indexes after a knowledge-base write."""
    row = conn.execute("SELECT value FROM kb_meta WHERE key='version'").fetchone()
    nxt = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT INTO kb_meta (key, value) VALUES ('version', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (str(nxt),),
    )
    return nxt


def kb_version() -> int:
    with connection() as conn:
        row = conn.execute("SELECT value FROM kb_meta WHERE key='version'").fetchone()
        return int(row["value"]) if row else 0


def row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    if isinstance(data.get("tags"), str):
        try:
            data["tags"] = json.loads(data["tags"])
        except json.JSONDecodeError:
            data["tags"] = []
    return data


def healthcheck() -> dict[str, Any]:
    """Confirm the database is reachable - used by /healthz on the hosted app."""
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"database": dialect(), "reachable": True}
