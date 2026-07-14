from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.config import settings
from backend.crud import create_db_definition, create_sql_feedback, create_sql_history, create_user
from backend.database import db_session, init_db
from backend.main import app
from backend.schemas import DbDefinitionCreate, UserCreateRequest


class AdminFeedbackRagApiTests(unittest.TestCase):
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
            self.admin = create_user(
                connection,
                UserCreateRequest(username="rag-admin", password="safe-password", role="admin"),
            )
            self.user = create_user(
                connection,
                UserCreateRequest(username="rag-user", password="safe-password"),
            )
            database = create_db_definition(
                connection,
                DbDefinitionCreate(name="admin-rag-db", db_type="mysql"),
                self.admin["id"],
            )
            history = create_sql_history(
                connection,
                user_id=self.user["id"],
                db_id=database["id"],
                natural_text="show active orders",
                target_db_type="mysql",
                generated_sql="SELECT * FROM orders",
            )
            self.feedback = create_sql_feedback(
                connection,
                history_id=history["id"],
                user_id=self.user["id"],
                feedback_type="modified",
                corrected_sql="SELECT id FROM orders WHERE status = 'active'",
            )
        self.db_id = database["id"]
        self.admin_token = self._token_for(self.admin)
        self.user_token = self._token_for(self.user)

    def tearDown(self) -> None:
        for settings_patch in reversed(self.settings_patches):
            settings_patch.stop()
        self.temporary_directory.cleanup()

    @staticmethod
    def _token_for(user: dict) -> str:
        return create_access_token(
            {
                "sub": str(user["id"]),
                "username": user["username"],
                "role": user["role"],
                "token_version": str(user["token_version"]),
            }
        )

    def test_admin_can_list_delete_examples_and_update_feedback_top_k(self) -> None:
        with patch("backend.main.sync_sql_feedback_rag_index"):
            with TestClient(app) as client:
                headers = {"Authorization": f"Bearer {self.admin_token}"}
                listing = client.get(
                    f"/api/feedback-rag/examples?db_id={self.db_id}",
                    headers=headers,
                )
                updated_config = client.put(
                    "/api/feedback-rag/config",
                    headers=headers,
                    json={"top_k": 5},
                )
                approved = client.post(
                    f"/api/feedback-rag/examples/{self.feedback['id']}/approve",
                    headers=headers,
                )
                approved_listing = client.get(
                    f"/api/feedback-rag/examples?db_id={self.db_id}&approved=true",
                    headers=headers,
                )
                pending_listing = client.get(
                    f"/api/feedback-rag/examples?db_id={self.db_id}&approved=false",
                    headers=headers,
                )
                deleted = client.delete(
                    f"/api/feedback-rag/examples/{self.feedback['id']}",
                    headers=headers,
                )

        self.assertEqual(listing.status_code, 200)
        self.assertFalse(listing.json()["items"][0]["approved"])
        self.assertEqual(listing.json()["items"][0]["corrected_sql"], self.feedback["corrected_sql"])
        self.assertEqual(updated_config.status_code, 200)
        self.assertEqual(updated_config.json()["top_k"], 5)
        self.assertEqual(approved.status_code, 204)
        self.assertEqual(approved_listing.json()["total"], 1)
        self.assertTrue(approved_listing.json()["items"][0]["approved"])
        self.assertEqual(pending_listing.json()["total"], 0)
        self.assertEqual(deleted.status_code, 204)

    def test_non_admin_cannot_manage_feedback_rag(self) -> None:
        with TestClient(app) as client:
            response = client.get(
                "/api/feedback-rag/examples",
                headers={"Authorization": f"Bearer {self.user_token}"},
            )

        self.assertEqual(response.status_code, 403)
