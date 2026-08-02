from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import settings
from backend.crud import (
    FeedbackValidationError,
    approve_sql_feedback,
    create_db_definition,
    create_sql_feedback,
    create_sql_history,
    create_user,
    replace_table_schema,
)
from backend.database import db_session, init_db
from backend.rag import retrieve_sql_feedback_context
from backend.schemas import (
    ColumnUpload,
    DbDefinitionCreate,
    TableUpload,
    TableUploadRequest,
    UserCreateRequest,
)


class FeedbackPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(settings, "db_path", Path(self.temp.name) / "test.db")
        self.patch.start()
        init_db()
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                self.admin = create_user(
                    connection,
                    UserCreateRequest(username="policy-admin", password="safe-password", role="admin"),
                )
                self.database = create_db_definition(
                    connection,
                    DbDefinitionCreate(name="policy-db", db_type="mysql"),
                    self.admin["id"],
                )
                replace_table_schema(
                    connection,
                    self.database["id"],
                    TableUploadRequest(
                        tables=[
                            TableUpload(
                                table_name="orders",
                                table_comment="订单",
                                columns=[
                                    ColumnUpload(column_name="id", data_type="INT"),
                                    ColumnUpload(column_name="status", data_type="VARCHAR(20)"),
                                    ColumnUpload(column_name="created_at", data_type="DATETIME"),
                                ],
                            ),
                            TableUpload(
                                table_name="customers",
                                table_comment="Customers",
                                columns=[
                                    ColumnUpload(column_name="id", data_type="INT"),
                                ],
                            ),
                        ]
                    ),
                )

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def _feedback(self, connection, sql: str, *, approved: bool = False, suffix: str = "") -> dict:
        history = create_sql_history(
            connection,
            user_id=self.admin["id"],
            db_id=self.database["id"],
            natural_text=f"show active orders {suffix}",
            target_db_type="mysql",
            generated_sql=sql,
        )
        return create_sql_feedback(
            connection,
            history_id=history["id"],
            user_id=self.admin["id"],
            feedback_type="correct",
            corrected_sql=None,
            approved=approved,
        )

    def test_admin_auto_approval_uses_policy_gate(self) -> None:
        with db_session() as connection:
            valid = self._feedback(connection, "SELECT id FROM orders", approved=True, suffix="valid")
        self.assertTrue(valid["approved"])

        with self.assertRaises(FeedbackValidationError):
            with db_session() as connection:
                self._feedback(connection, "DELETE FROM orders", approved=True, suffix="invalid")

    def test_explicit_approval_uses_same_policy_gate(self) -> None:
        with db_session() as connection:
            feedback = self._feedback(connection, "SELECT secret FROM orders", suffix="unknown column")
            with self.assertRaises(FeedbackValidationError):
                approve_sql_feedback(connection, feedback["id"])

    def test_legacy_approved_feedback_is_lazily_revalidated(self) -> None:
        with db_session() as connection:
            feedback = self._feedback(connection, "SELECT * FROM missing_table", suffix="legacy")
            connection.execute("UPDATE sql_feedback SET approved = 1 WHERE id = ?", (feedback["id"],))

        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                context = retrieve_sql_feedback_context(
                    connection,
                    db_id=self.database["id"],
                    question="show active orders",
                    target_db_type="mysql",
                )
        self.assertEqual(context, {"examples": [], "retrieval_mode": "empty"})

    def test_feedback_with_missing_aggregate_is_rejected_before_rag(self) -> None:
        with db_session() as connection:
            history = create_sql_history(
                connection,
                user_id=self.admin["id"],
                db_id=self.database["id"],
                natural_text="count active orders",
                target_db_type="mysql",
                generated_sql="SELECT id FROM orders",
            )
            feedback = create_sql_feedback(
                connection,
                history_id=history["id"],
                user_id=self.admin["id"],
                feedback_type="modified",
                corrected_sql="SELECT id FROM orders",
            )

            with self.assertRaises(FeedbackValidationError) as error:
                approve_sql_feedback(connection, feedback["id"])

        self.assertEqual(error.exception.issues[0]["code"], "INTENT_AGGREGATE_MISSING")

    def test_feedback_using_an_unrelated_schema_table_is_rejected_before_rag(self) -> None:
        with db_session() as connection:
            history = create_sql_history(
                connection,
                user_id=self.admin["id"],
                db_id=self.database["id"],
                natural_text="show active orders",
                target_db_type="mysql",
                generated_sql="SELECT id FROM customers",
            )
            feedback = create_sql_feedback(
                connection,
                history_id=history["id"],
                user_id=self.admin["id"],
                feedback_type="modified",
                corrected_sql="SELECT id FROM customers",
            )

            with self.assertRaises(FeedbackValidationError) as error:
                approve_sql_feedback(connection, feedback["id"])

        self.assertEqual(error.exception.issues[0]["code"], "OUTSIDE_RETRIEVED_EVIDENCE")

    def test_feedback_missing_requested_time_filter_is_rejected_before_rag(self) -> None:
        with db_session() as connection:
            history = create_sql_history(
                connection,
                user_id=self.admin["id"],
                db_id=self.database["id"],
                natural_text="show orders from 2025-01-01 to 2025-01-31",
                target_db_type="mysql",
                generated_sql="SELECT id FROM orders WHERE status = 'active'",
            )
            feedback = create_sql_feedback(
                connection,
                history_id=history["id"],
                user_id=self.admin["id"],
                feedback_type="modified",
                corrected_sql="SELECT id FROM orders WHERE status = 'active'",
            )

            with self.assertRaises(FeedbackValidationError) as error:
                approve_sql_feedback(connection, feedback["id"])

        self.assertEqual(error.exception.issues[0]["code"], "INTENT_TIME_RANGE_MISSING")


if __name__ == "__main__":
    unittest.main()
