from __future__ import annotations

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
from backend.schemas import ColumnUpload, DbDefinitionCreate, TableUpload, TableUploadRequest, UserCreateRequest


class HisSemanticsApiTests(unittest.TestCase):
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
                UserCreateRequest(username="his-admin", password="safe-password", role="admin"),
            )
            self.user = create_user(
                connection,
                UserCreateRequest(username="his-user", password="safe-password"),
            )
            database = create_db_definition(
                connection,
                DbDefinitionCreate(name="his-api-db", db_type="mysql"),
                self.admin["id"],
            )
            with patch("backend.rag._vector_search_available", return_value=False):
                replace_table_schema(
                    connection,
                    database["id"],
                    TableUploadRequest(
                        tables=[
                            TableUpload(
                                table_name="patients",
                                columns=[
                                    ColumnUpload(column_name="id", data_type="INT"),
                                    ColumnUpload(column_name="admitted_at", data_type="DATETIME"),
                                ],
                            )
                        ]
                    ),
                )
        self.db_id = database["id"]
        self.admin_headers = {"Authorization": f"Bearer {self._token_for(self.admin)}"}
        self.user_headers = {"Authorization": f"Bearer {self._token_for(self.user)}"}

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

    def _payload(self, **overrides) -> dict:
        payload = {
            "db_id": self.db_id,
            "term": "admission",
            "synonyms": ["admit"],
            "definition": "Patient hospital admission event.",
            "category": "event",
            "bindings": [{"table": "patients", "columns": ["id"], "role": "patient record"}],
            "sql_hint": "Use the patients table.",
            "enabled": True,
        }
        payload.update(overrides)
        return payload

    def test_admin_can_create_list_update_and_delete_terms(self) -> None:
        with TestClient(app) as client:
            created = client.post("/api/his-terms", headers=self.admin_headers, json=self._payload())
            duplicate = client.post("/api/his-terms", headers=self.admin_headers, json=self._payload(term="Admission"))
            listed = client.get(
                "/api/his-terms",
                headers=self.admin_headers,
                params={"db_id": self.db_id, "search": "admit", "category": "event"},
            )

            self.assertEqual(created.status_code, 201, created.text)
            term = created.json()
            self.assertEqual(term["bindings"], [{"table": "patients", "columns": ["id"], "role": "patient record"}])
            self.assertEqual(duplicate.status_code, 409, duplicate.text)
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["total"], 1)
            self.assertEqual(listed.json()["items"][0]["id"], term["id"])

            updated = client.put(
                f"/api/his-terms/{term['id']}",
                headers=self.admin_headers,
                json=self._payload(
                    synonyms=["admitted"],
                    definition="Patient admission or registration event.",
                    bindings=[{"table": "patients", "columns": ["admitted_at"], "role": "event time"}],
                    enabled=False,
                ),
            )
            disabled = client.get(
                "/api/his-terms",
                headers=self.admin_headers,
                params={"db_id": self.db_id, "enabled": "false"},
            )
            deleted = client.delete(f"/api/his-terms/{term['id']}", headers=self.admin_headers)
            missing = client.delete(f"/api/his-terms/{term['id']}", headers=self.admin_headers)

        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertFalse(updated.json()["enabled"])
        self.assertEqual(updated.json()["bindings"][0]["columns"], ["admitted_at"])
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(disabled.json()["total"], 1)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(missing.status_code, 404)

    def test_invalid_schema_binding_is_rejected(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/his-terms",
                headers=self.admin_headers,
                json=self._payload(bindings=[{"table": "missing_table", "columns": ["id"]}]),
            )

        self.assertEqual(response.status_code, 400, response.text)

    def test_non_admin_cannot_manage_terms(self) -> None:
        with TestClient(app) as client:
            listing = client.get("/api/his-terms", headers=self.user_headers)
            creation = client.post("/api/his-terms", headers=self.user_headers, json=self._payload())

        self.assertEqual(listing.status_code, 403)
        self.assertEqual(creation.status_code, 403)


if __name__ == "__main__":
    unittest.main()
