from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
    token_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS db_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    db_type TEXT NOT NULL CHECK(db_type IN ('mysql', 'pg', 'oracle')),
    created_by INTEGER NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS table_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    db_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    table_comment TEXT NOT NULL DEFAULT '',
    UNIQUE(db_id, table_name),
    FOREIGN KEY (db_id) REFERENCES db_definitions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS column_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    column_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    column_comment TEXT NOT NULL DEFAULT '',
    UNIQUE(table_id, column_name),
    FOREIGN KEY (table_id) REFERENCES table_meta(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS table_relation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    db_id INTEGER NOT NULL,
    from_table_id INTEGER NOT NULL,
    from_column TEXT NOT NULL,
    to_table_id INTEGER NOT NULL,
    to_column TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    FOREIGN KEY (db_id) REFERENCES db_definitions(id) ON DELETE CASCADE,
    FOREIGN KEY (from_table_id) REFERENCES table_meta(id) ON DELETE CASCADE,
    FOREIGN KEY (to_table_id) REFERENCES table_meta(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sql_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    db_id INTEGER NOT NULL,
    natural_text TEXT NOT NULL,
    target_db_type TEXT NOT NULL CHECK(target_db_type IN ('mysql', 'pg', 'oracle')),
    generated_sql TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (db_id) REFERENCES db_definitions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sql_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id INTEGER UNIQUE,
    user_id INTEGER NOT NULL,
    db_id INTEGER NOT NULL,
    natural_text TEXT NOT NULL,
    target_db_type TEXT NOT NULL CHECK(target_db_type IN ('mysql', 'pg', 'oracle')),
    generated_sql TEXT NOT NULL,
    corrected_sql TEXT NOT NULL,
    feedback_type TEXT NOT NULL CHECK(feedback_type IN ('correct', 'modified')),
    approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0, 1)),
    created_at TEXT NOT NULL,
    FOREIGN KEY (history_id) REFERENCES sql_history(id) ON DELETE SET NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (db_id) REFERENCES db_definitions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sql_feedback_db_type
ON sql_feedback(db_id, target_db_type, id DESC);

CREATE TABLE IF NOT EXISTS schema_rag_index (
    db_id INTEGER NOT NULL,
    table_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    table_comment TEXT NOT NULL DEFAULT '',
    retrieval_text TEXT NOT NULL,
    ddl_sql TEXT NOT NULL,
    foreign_keys_json TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    PRIMARY KEY (db_id, table_id),
    FOREIGN KEY (db_id) REFERENCES db_definitions(id) ON DELETE CASCADE,
    FOREIGN KEY (table_id) REFERENCES table_meta(id) ON DELETE CASCADE
);
"""


def get_connection() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with db_session() as connection:
        connection.executescript(SCHEMA_SQL)
        _run_migrations(connection)

        from .crud import ensure_default_admin

        ensure_default_admin(connection)


def _column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    cursor = connection.execute(f"PRAGMA table_info({table_name})")
    return any(row["name"] == column_name for row in cursor.fetchall())


def _run_migrations(connection: sqlite3.Connection) -> None:
    if not _column_exists(connection, "users", "token_version"):
        connection.execute(
            """
            ALTER TABLE users
            ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0
            """
        )

    if not _column_exists(connection, "sql_history", "retrieved_tables_json"):
        connection.execute(
            """
            ALTER TABLE sql_history
            ADD COLUMN retrieved_tables_json TEXT NOT NULL DEFAULT '[]'
            """
        )

    if not _column_exists(connection, "sql_feedback", "approved"):
        connection.execute(
            """
            ALTER TABLE sql_feedback
            ADD COLUMN approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0, 1))
            """
        )
