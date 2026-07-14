from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.config import settings
from backend.crud import create_db_definition, create_sql_history, create_user
from backend.database import db_session, init_db
from backend.main import app
from backend.schemas import DbDefinitionCreate, UserCreateRequest


class SqlFeedbackApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.settings_patches = [
            patch.object(settings, "db_path", database_path),
            patch.object(settings, "app_host", "127.0.0.1"),
        ]
        for settings_patch in self.settings_patches:
            settings_patch.start()
        init_db()

        with db_session() as connection:
            user = create_user(
                connection,
                UserCreateRequest(username="api-user", password="safe-password"),
            )
            database = create_db_definition(
                connection,
                DbDefinitionCreate(name="api-db", db_type="mysql"),
                user["id"],
            )
            self.history = create_sql_history(
                connection,
                user_id=user["id"],
                db_id=database["id"],
                natural_text="show active orders",
                target_db_type="mysql",
                generated_sql="SELECT * FROM orders",
            )
        self.token = create_access_token(
            {
                "sub": str(user["id"]),
                "username": user["username"],
                "role": user["role"],
                "token_version": str(user["token_version"]),
            }
        )

    def tearDown(self) -> None:
        for settings_patch in reversed(self.settings_patches):
            settings_patch.stop()
        self.temporary_directory.cleanup()

    def test_feedback_endpoint_saves_a_modified_sql_example(self) -> None:
        with patch("backend.main.sync_sql_feedback_rag_index"):
            with TestClient(app) as client:
                response = client.post(
                    "/api/sql-feedback",
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={
                        "history_id": self.history["id"],
                        "feedback_type": "modified",
                        "corrected_sql": "SELECT id FROM orders WHERE status = 'active'",
                    },
                )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["feedback_type"], "modified")
        self.assertEqual(
            response.json()["corrected_sql"],
            "SELECT id FROM orders WHERE status = 'active'",
        )
