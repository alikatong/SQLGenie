from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import settings, validate_qwen_embedding_model_path
from backend.crud import create_db_definition, get_model_runtime_config, replace_table_schema, update_model_config
from backend.database import db_session, init_db
from backend.rag import initialize_database_rag
from backend.schemas import ConfigUpdate, DbDefinitionCreate, TableUploadRequest


class EmbeddingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "test.db"
        self.model_path = Path(self.temp.name) / "Qwen3-Embedding-0.6B"
        self.model_path.mkdir()
        (self.model_path / "config.json").write_text(
            json.dumps({"_name_or_path": "Qwen/Qwen3-Embedding-0.6B"}),
            encoding="utf-8",
        )
        self.settings_patches = [
            patch.object(settings, "db_path", self.database_path),
            patch.object(settings, "rag_embedding_model", str(self.model_path)),
        ]
        for settings_patch in self.settings_patches:
            settings_patch.start()
        init_db()

    def tearDown(self) -> None:
        for settings_patch in reversed(self.settings_patches):
            settings_patch.stop()
        self.temp.cleanup()

    def test_qwen_local_model_path_is_persisted_and_exposed_without_api_key(self) -> None:
        with db_session() as connection:
            view = update_model_config(
                connection,
                ConfigUpdate(
                    api_key="sk-test-secret",
                    base_url="https://example.test/v1",
                    model_name="test-model",
                    embedding_model_path=str(self.model_path),
                ),
            )
            runtime = get_model_runtime_config(connection)

        self.assertEqual(runtime["embedding_model_path"], str(self.model_path))
        self.assertEqual(view["embedding_model_path"], str(self.model_path))
        self.assertEqual(view["embedding_model_family"], "Qwen")
        self.assertNotIn("api_key", view)

    def test_non_qwen_or_missing_local_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_qwen_embedding_model_path(str(Path(self.temp.name) / "missing-qwen"))

        non_qwen_path = Path(self.temp.name) / "bge-small"
        non_qwen_path.mkdir()
        (non_qwen_path / "config.json").write_text(
            json.dumps({"_name_or_path": "BAAI/bge-small-zh-v1.5"}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            validate_qwen_embedding_model_path(str(non_qwen_path))

    def test_runtime_config_read_does_not_mutate_global_settings(self) -> None:
        with db_session() as connection:
            update_model_config(
                connection,
                ConfigUpdate(
                    base_url="https://example.test/v1",
                    model_name="test-model",
                    embedding_model_path=str(self.model_path),
                ),
            )

        settings.rag_embedding_model = "sentinel-path"
        with db_session() as connection:
            get_model_runtime_config(connection)

        self.assertEqual(settings.rag_embedding_model, "sentinel-path")
        settings.rag_embedding_model = str(self.model_path)

    def test_initialization_passes_model_path_into_sync(self) -> None:
        captured: dict[str, object] = {}

        def fake_sync_schema(connection, *, schema_bundle, force=False, strict=False, embedding_model_path=None):
            captured["schema_path"] = embedding_model_path
            return []

        def fake_sync_feedback(connection, db_id, *, strict=False, embedding_model_path=None):
            captured["feedback_path"] = embedding_model_path

        with (
            patch("backend.rag._vector_search_available", return_value=True),
            patch("backend.rag.ensure_embedding_runtime", return_value=None) as ensure_runtime,
            patch("backend.rag.sync_schema_rag_index", side_effect=fake_sync_schema),
            patch("backend.rag.sync_sql_feedback_rag_index", side_effect=fake_sync_feedback),
            patch("backend.rag._load_index_rows", return_value=[]),
            patch("backend.rag._load_sql_feedback_rows", return_value=[]),
        ):
            initialize_database_rag(
                None,
                {"db_definition": {"id": 1}},
                embedding_model_path=str(self.model_path),
            )

        ensure_runtime.assert_called_once_with(str(self.model_path))
        self.assertEqual(captured["schema_path"], str(self.model_path))
        self.assertEqual(captured["feedback_path"], str(self.model_path))

    def test_schema_sync_uses_the_persisted_embedding_model_path(self) -> None:
        with db_session() as connection:
            database = create_db_definition(
                connection,
                DbDefinitionCreate(name="schema-db", db_type="mysql"),
                created_by=1,
            )
            update_model_config(
                connection,
                ConfigUpdate(
                    base_url="https://example.test/v1",
                    model_name="test-model",
                    embedding_model_path=str(self.model_path),
                ),
            )
            with patch("backend.crud.sync_schema_rag_index") as sync_schema:
                replace_table_schema(
                    connection,
                    database["id"],
                    TableUploadRequest(),
                )

        self.assertEqual(
            sync_schema.call_args.kwargs["embedding_model_path"],
            str(self.model_path),
        )

    def test_initialization_requires_vector_runtime(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "依赖不可用"):
                initialize_database_rag(
                    None,
                    {"db_definition": {"id": 1}},
                )


if __name__ == "__main__":
    unittest.main()
