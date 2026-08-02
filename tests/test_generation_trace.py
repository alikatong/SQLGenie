from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import settings
from backend.crud import (
    create_db_definition,
    create_generation_trace,
    create_sql_history,
    create_user,
)
from backend.database import db_session, init_db
from backend.schemas import DbDefinitionCreate, UserCreateRequest


class GenerationTraceStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(settings, "db_path", Path(self.temp.name) / "test.db")
        self.patch.start()
        init_db()
        with db_session() as connection:
            self.user = create_user(connection, UserCreateRequest(username="trace-user", password="safe-password"))
            self.database = create_db_definition(
                connection,
                DbDefinitionCreate(name="trace-db", db_type="mysql"),
                self.user["id"],
            )

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_trace_links_history_and_stores_structured_metadata_only(self) -> None:
        with db_session() as connection:
            history = create_sql_history(
                connection,
                user_id=self.user["id"],
                db_id=self.database["id"],
                natural_text="列出订单",
                target_db_type="mysql",
                generated_sql="SELECT id FROM orders",
            )
            trace = create_generation_trace(
                connection,
                {
                    "request_id": "request-1",
                    "history_id": history["id"],
                    "user_id": self.user["id"],
                    "db_id": self.database["id"],
                    "prompt_version": "his-sql-v1",
                    "policy_version": "sql-policy-v1",
                    "context_hash": "a" * 64,
                    "model_name": "model",
                    "retrieval_mode": "keyword",
                    "retrieved_tables_json": [{"table_name": "orders"}],
                    "retrieved_terms_json": [],
                    "policy_status": "passed",
                    "validation_errors_json": [],
                    "warnings_json": [],
                    "model_calls": 1,
                    "outcome": "passed",
                    "duration_ms": 12,
                    "prompt_chars": 800,
                },
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(generation_trace)")}

        self.assertEqual(trace["request_id"], "request-1")
        self.assertEqual(trace["history_id"], history["id"])
        self.assertNotIn("prompt", columns)
        self.assertNotIn("raw_response", columns)
        self.assertNotIn("api_key", columns)


if __name__ == "__main__":
    unittest.main()
