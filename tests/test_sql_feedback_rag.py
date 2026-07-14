from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import settings
from backend.crud import approve_sql_feedback, create_db_definition, create_sql_feedback, create_sql_history, create_user
from backend.database import db_session, init_db
from backend.llm import SQL_GENERATION_TEMPERATURE, _build_rag_prompt
from backend.rag import retrieve_sql_feedback_context, sync_sql_feedback_rag_index
from backend.schemas import DbDefinitionCreate, UserCreateRequest


class SqlFeedbackRagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.settings_patch = patch.object(settings, "db_path", database_path)
        self.settings_patch.start()
        init_db()

        with db_session() as connection:
            user = create_user(
                connection,
                UserCreateRequest(username="rag-user", password="safe-password"),
            )
            database = create_db_definition(
                connection,
                DbDefinitionCreate(name="rag-db", db_type="mysql"),
                user["id"],
            )
            history = create_sql_history(
                connection,
                user_id=user["id"],
                db_id=database["id"],
                natural_text="show active orders",
                target_db_type="mysql",
                generated_sql="SELECT * FROM orders",
            )
            self.feedback = create_sql_feedback(
                connection,
                history_id=history["id"],
                user_id=user["id"],
                feedback_type="modified",
                corrected_sql="SELECT id, status FROM orders WHERE status = 'active'",
            )
        self.db_id = database["id"]

    def tearDown(self) -> None:
        self.settings_patch.stop()
        self.temporary_directory.cleanup()

    def test_keyword_retrieval_returns_verified_correction_examples(self) -> None:
        with db_session() as connection:
            approve_sql_feedback(connection, self.feedback["id"])

        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                context = retrieve_sql_feedback_context(
                    connection,
                    db_id=self.db_id,
                    question="find active orders",
                    target_db_type="mysql",
                )

        self.assertEqual(context["retrieval_mode"], "keyword")
        self.assertEqual(len(context["examples"]), 1)
        self.assertEqual(
            context["examples"][0]["corrected_sql"],
            "SELECT id, status FROM orders WHERE status = 'active'",
        )

    def test_unapproved_feedback_is_not_used_for_prompt_context(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                context = retrieve_sql_feedback_context(
                    connection,
                    db_id=self.db_id,
                    question="find active orders",
                    target_db_type="mysql",
                )

        self.assertEqual(context, {"examples": [], "retrieval_mode": "empty"})

    def test_confirmed_sql_is_retrieved_and_added_to_the_next_prompt(self) -> None:
        with db_session() as connection:
            user = create_user(
                connection,
                UserCreateRequest(username="confirmed-user", password="safe-password"),
            )
            history = create_sql_history(
                connection,
                user_id=user["id"],
                db_id=self.db_id,
                natural_text="list order identifiers",
                target_db_type="mysql",
                generated_sql="SELECT id FROM orders",
            )
            create_sql_feedback(
                connection,
                history_id=history["id"],
                user_id=user["id"],
                feedback_type="correct",
                corrected_sql=None,
            )

            feedback = connection.execute(
                "SELECT id FROM sql_feedback WHERE history_id = ?",
                (history["id"],),
            ).fetchone()
            approve_sql_feedback(connection, feedback["id"])

        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                context = retrieve_sql_feedback_context(
                    connection,
                    db_id=self.db_id,
                    question="find order identifiers",
                    target_db_type="mysql",
                )

        prompt = _build_rag_prompt(
            target_db_type="mysql",
            operation="SELECT",
            question="find order identifiers",
            retrieved_tables_ddl="CREATE TABLE orders (id INT);",
            feedback_examples=context["examples"],
        )

        self.assertIn("SELECT id FROM orders", prompt)

    def test_generation_prompt_includes_verified_corrections_and_uses_low_temperature(self) -> None:
        prompt = _build_rag_prompt(
            target_db_type="mysql",
            operation="SELECT",
            question="find active orders",
            retrieved_tables_ddl="CREATE TABLE orders (id INT, status VARCHAR(20));",
            feedback_examples=[
                {
                    "natural_text": "show active orders",
                    "corrected_sql": "SELECT id, status FROM orders WHERE status = 'active'",
                }
            ],
        )

        self.assertIn("SELECT id, status FROM orders WHERE status = 'active'", prompt)
        self.assertEqual(SQL_GENERATION_TEMPERATURE, 0.1)

    def test_embedding_sync_writes_verified_feedback_to_a_separate_collection(self) -> None:
        class FakeCollection:
            def __init__(self) -> None:
                self.added = None

            def add(self, **kwargs) -> None:
                self.added = kwargs

        class FakeClient:
            def __init__(self) -> None:
                self.collection = FakeCollection()
                self.deleted_collections: list[str] = []

            def delete_collection(self, name: str) -> None:
                self.deleted_collections.append(name)

            def get_or_create_collection(self, **_kwargs) -> FakeCollection:
                return self.collection

        client = FakeClient()
        with db_session() as connection:
            approve_sql_feedback(connection, self.feedback["id"])

        with (
            patch("backend.rag._vector_search_available", return_value=True),
            patch("backend.rag._get_chroma_client", return_value=client),
        ):
            with db_session() as connection:
                sync_sql_feedback_rag_index(connection, self.db_id)

        self.assertTrue(client.deleted_collections[0].endswith(f"db_{self.db_id}_feedback"))
        self.assertIn("show active orders", client.collection.added["documents"][0])
        self.assertIn("WHERE status = 'active'", client.collection.added["documents"][0])
