from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.config import settings
from backend.crud import create_db_definition, create_user, replace_table_schema
from backend.database import db_session, init_db
from backend.main import app
from backend.schemas import (
    ColumnUpload,
    DbDefinitionCreate,
    TableUpload,
    TableUploadRequest,
    UserCreateRequest,
)


class EmbeddingRagApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp.name) / "Qwen3-Embedding-0.6B"
        self.model_path.mkdir()
        (self.model_path / "config.json").write_text(
            json.dumps({"model_type": "qwen3"}),
            encoding="utf-8",
        )
        self.settings_patches = [
            patch.object(settings, "db_path", Path(self.temp.name) / "test.db"),
            patch.object(settings, "app_host", "127.0.0.1"),
            patch.object(settings, "rag_embedding_model", str(self.model_path)),
        ]
        for settings_patch in self.settings_patches:
            settings_patch.start()
        init_db()

        with db_session() as connection:
            self.admin = create_user(
                connection,
                UserCreateRequest(username="embedding-admin", password="safe-password", role="admin"),
            )
            self.user = create_user(
                connection,
                UserCreateRequest(username="embedding-user", password="safe-password"),
            )
            self.database_ids = []
            for index in range(2):
                database = create_db_definition(
                    connection,
                    DbDefinitionCreate(name=f"embedding-db-{index}", db_type="mysql"),
                    self.admin["id"],
                )
                replace_table_schema(
                    connection,
                    database["id"],
                    TableUploadRequest(
                        tables=[
                            TableUpload(
                                table_name=f"orders_{index}",
                                columns=[ColumnUpload(column_name="id", data_type="INT")],
                            )
                        ]
                    ),
                )
                self.database_ids.append(database["id"])

        self.admin_token = self._token_for(self.admin)
        self.user_token = self._token_for(self.user)

    def tearDown(self) -> None:
        for settings_patch in reversed(self.settings_patches):
            settings_patch.stop()
        self.temp.cleanup()

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

    def test_admin_can_initialize_all_database_rag_indexes(self) -> None:
        initialized: list[int] = []

        def fake_initialize(
            _connection,
            schema_bundle: dict,
            *,
            embedding_model_path: str | None = None,
        ) -> dict[str, int]:
            db_id = int(schema_bundle["db_definition"]["id"])
            initialized.append(db_id)
            return {"table_count": 1, "feedback_example_count": 2}

        with patch("backend.main.initialize_database_rag", side_effect=fake_initialize):
            with TestClient(app) as client:
                response = client.post(
                    "/api/embedding-rag/initialize",
                    headers={"Authorization": f"Bearer {self.admin_token}"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(initialized), sorted(self.database_ids))
        self.assertEqual(response.json()["database_count"], 2)
        self.assertEqual(response.json()["schema_table_count"], 2)
        self.assertEqual(response.json()["feedback_example_count"], 4)
        self.assertEqual(response.json()["failed_databases"], [])

    def test_non_admin_cannot_initialize_database_rag(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/embedding-rag/initialize",
                headers={"Authorization": f"Bearer {self.user_token}"},
            )

        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_open_embedding_model_picker(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/embedding-models/pick-directory",
                headers={"Authorization": f"Bearer {self.user_token}"},
            )

        self.assertEqual(response.status_code, 403)

    def test_invalid_embedding_model_path_returns_bad_request(self) -> None:
        with TestClient(app) as client:
            response = client.put(
                "/api/config",
                headers={"Authorization": f"Bearer {self.admin_token}"},
                json={
                    "base_url": "https://example.test/v1",
                    "model_name": "test-model",
                    "embedding_model_path": str(Path(self.temp.name) / "missing-qwen"),
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_EMBEDDING_MODEL")

    def test_admin_can_choose_embedding_model_directory(self) -> None:
        with patch("backend.main.pick_qwen_embedding_model_path", return_value=str(self.model_path)) as picker:
            with TestClient(app) as client:
                response = client.post(
                    "/api/embedding-models/pick-directory",
                    headers={"Authorization": f"Bearer {self.admin_token}"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["selected"])
        self.assertEqual(response.json()["embedding_model_path"], str(self.model_path))
        picker.assert_called_once()

    def test_cancelled_directory_selection_keeps_current_path(self) -> None:
        with patch("backend.main.pick_qwen_embedding_model_path", return_value=None):
            with TestClient(app) as client:
                response = client.post(
                    "/api/embedding-models/pick-directory",
                    headers={"Authorization": f"Bearer {self.admin_token}"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["selected"])
        self.assertEqual(response.json()["embedding_model_path"], str(self.model_path))

    def test_directory_picker_failure_returns_service_error(self) -> None:
        with patch(
            "backend.main.pick_qwen_embedding_model_path",
            side_effect=RuntimeError("native picker unavailable"),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/embedding-models/pick-directory",
                    headers={"Authorization": f"Bearer {self.admin_token}"},
                )

        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["detail"]["code"], "MODEL_DIRECTORY_PICKER_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
