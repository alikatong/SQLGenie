from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from backend import rag
from backend.database import SCHEMA_SQL


class FeedbackValidationCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA_SQL)
        self.connection.execute(
            """
            INSERT INTO users (id, username, password, role, token_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1, "user", "hash", "user", 0, "now"),
        )
        self.connection.execute(
            """
            INSERT INTO db_definitions (id, name, db_type, created_by)
            VALUES (?, ?, ?, ?)
            """,
            (1, "db", "mysql", 1),
        )
        self.connection.execute(
            """
            INSERT INTO sql_feedback (
                id, history_id, user_id, db_id, natural_text, target_db_type,
                generated_sql, corrected_sql, feedback_type, approved, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                None,
                1,
                1,
                "list orders",
                "mysql",
                "SELECT id FROM orders",
                "SELECT id FROM orders",
                "correct",
                1,
                "now",
            ),
        )
        with rag._FEEDBACK_VALIDATION_CACHE_LOCK:
            rag._FEEDBACK_VALIDATION_CACHE.clear()

    def tearDown(self) -> None:
        self.connection.close()
        with rag._FEEDBACK_VALIDATION_CACHE_LOCK:
            rag._FEEDBACK_VALIDATION_CACHE.clear()

    def test_repeated_load_reuses_validation_cache(self) -> None:
        calls = 0

        def fake_validate(connection, row, *, schema_bundle=None):
            nonlocal calls
            calls += 1
            return True, []

        with patch("backend.rag.validate_feedback_for_rag", side_effect=fake_validate):
            first = rag._load_sql_feedback_rows(self.connection, 1)
            second = rag._load_sql_feedback_rows(self.connection, 1)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(calls, 1)

    def test_schema_change_invalidates_feedback_cache(self) -> None:
        self.connection.execute(
            """
            INSERT INTO table_meta (id, db_id, table_name, table_comment)
            VALUES (?, ?, ?, ?)
            """,
            (1, 1, "orders", ""),
        )
        self.connection.execute(
            """
            INSERT INTO schema_rag_index (
                db_id, table_id, table_name, table_comment, retrieval_text,
                ddl_sql, foreign_keys_json, content_hash, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 1, "orders", "", "orders", "CREATE TABLE orders (id INT);", "[]", "hash-v1", "now"),
        )
        calls = 0

        def fake_validate(connection, row, *, schema_bundle=None):
            nonlocal calls
            calls += 1
            return True, []

        with patch("backend.rag.validate_feedback_for_rag", side_effect=fake_validate):
            rag._load_sql_feedback_rows(self.connection, 1)
            first_calls = calls
            self.connection.execute(
                "UPDATE schema_rag_index SET content_hash = ? WHERE db_id = ?",
                ("hash-v2", 1),
            )
            rag._load_sql_feedback_rows(self.connection, 1)

        self.assertEqual(first_calls, 1)
        self.assertEqual(calls, 2)

    def test_semantic_term_change_invalidates_feedback_cache(self) -> None:
        calls = 0

        def fake_validate(connection, row, *, schema_bundle=None):
            nonlocal calls
            calls += 1
            return True, []

        with patch("backend.rag.validate_feedback_for_rag", side_effect=fake_validate):
            rag._load_sql_feedback_rows(self.connection, 1)
            self.connection.execute(
                """
                INSERT INTO his_semantic_term (
                    id, db_id, term, synonyms_json, definition, category,
                    bindings_json, sql_hint, enabled, created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, 1, "订单", "[]", "订单业务词", "entity", "[]", "", 1, 1, "now", "now"),
            )
            rag._load_sql_feedback_rows(self.connection, 1)

        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
