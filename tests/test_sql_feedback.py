from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import settings
from backend.crud import (
    create_db_definition,
    create_sql_feedback,
    create_sql_history,
    create_user,
)
from backend.database import db_session, init_db
from backend.schemas import DbDefinitionCreate, UserCreateRequest


class SqlFeedbackStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        self.settings_patch = patch.object(settings, "db_path", self.database_path)
        self.settings_patch.start()
        init_db()

        with db_session() as connection:
            self.user = create_user(
                connection,
                UserCreateRequest(username="feedback-user", password="safe-password"),
            )
            self.other_user = create_user(
                connection,
                UserCreateRequest(username="other-user", password="safe-password"),
            )
            self.database = create_db_definition(
                connection,
                DbDefinitionCreate(name="feedback-db", db_type="mysql"),
                self.user["id"],
            )
            self.history = create_sql_history(
                connection,
                user_id=self.user["id"],
                db_id=self.database["id"],
                natural_text="show active orders",
                target_db_type="mysql",
                generated_sql="SELECT * FROM orders",
            )

    def tearDown(self) -> None:
        self.settings_patch.stop()
        self.temporary_directory.cleanup()

    def test_correct_feedback_stores_the_generated_sql_as_a_verified_example(self) -> None:
        with db_session() as connection:
            feedback = create_sql_feedback(
                connection,
                history_id=self.history["id"],
                user_id=self.user["id"],
                feedback_type="correct",
                corrected_sql=None,
            )

        self.assertEqual(feedback["feedback_type"], "correct")
        self.assertEqual(feedback["generated_sql"], "SELECT * FROM orders")
        self.assertEqual(feedback["corrected_sql"], "SELECT * FROM orders")

    def test_modified_feedback_stores_the_user_correction(self) -> None:
        corrected_sql = "SELECT id, status FROM orders WHERE status = 'active'"

        with db_session() as connection:
            feedback = create_sql_feedback(
                connection,
                history_id=self.history["id"],
                user_id=self.user["id"],
                feedback_type="modified",
                corrected_sql=corrected_sql,
            )

        self.assertEqual(feedback["feedback_type"], "modified")
        self.assertEqual(feedback["generated_sql"], "SELECT * FROM orders")
        self.assertEqual(feedback["corrected_sql"], corrected_sql)

    def test_feedback_cannot_be_submitted_for_another_users_history(self) -> None:
        with db_session() as connection:
            feedback = create_sql_feedback(
                connection,
                history_id=self.history["id"],
                user_id=self.other_user["id"],
                feedback_type="correct",
                corrected_sql=None,
            )

        self.assertIsNone(feedback)
