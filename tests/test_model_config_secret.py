from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import settings
from backend.crud import get_model_config_view, get_model_runtime_config, update_model_config
from backend.database import db_session, init_db
from backend.schemas import ConfigUpdate, ConfigView


class ModelConfigSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(settings, "db_path", Path(self.temp.name) / "test.db")
        self.patch.start()
        init_db()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_view_type_has_no_api_key_field(self) -> None:
        self.assertNotIn("api_key", ConfigView.model_fields)

    def test_default_timeout_is_ten_minutes(self) -> None:
        payload = ConfigUpdate(base_url="https://example.test/v1", model_name="test-model")

        self.assertEqual(ConfigView().thinking_timeout_seconds, 600)
        self.assertEqual(payload.thinking_timeout_seconds, 600)
        self.assertEqual(ConfigView().prompt_max_chars, 60_000)
        self.assertEqual(payload.prompt_max_chars, 60_000)
        self.assertIsNone(ConfigView().reasoning_effort)
        self.assertIsNone(payload.reasoning_effort)

    def test_schema_rag_top_k_is_persisted_and_exposed(self) -> None:
        with db_session() as connection:
            view = update_model_config(
                connection,
                ConfigUpdate(
                    base_url="https://example.test/v1",
                    model_name="test-model",
                    rag_top_k=12,
                    prompt_max_chars=60_000,
                ),
            )
            runtime = get_model_runtime_config(connection)

        self.assertEqual(runtime["rag_top_k"], 12)
        self.assertEqual(view["rag_top_k"], 12)
        self.assertEqual(runtime["prompt_max_chars"], 60_000)
        self.assertEqual(view["prompt_max_chars"], 60_000)

    def test_embedding_model_path_is_optional_for_legacy_updates(self) -> None:
        with db_session() as connection:
            update_model_config(
                connection,
                ConfigUpdate(
                    base_url="https://example.test/v1",
                    model_name="test-model",
                ),
            )
            runtime = get_model_runtime_config(connection)

        self.assertEqual(runtime["embedding_model_path"], settings.rag_embedding_model)

    def test_view_only_returns_status_and_last_four(self) -> None:
        with db_session() as connection:
            update_model_config(
                connection,
                ConfigUpdate(
                    api_key="sk-secret-1234",
                    base_url="https://example.test/v1",
                    model_name="test-model",
                ),
            )
            runtime = get_model_runtime_config(connection)
            view = get_model_config_view(connection)

        self.assertEqual(runtime["api_key"], "sk-secret-1234")
        self.assertNotIn("api_key", view)
        self.assertTrue(view["api_key_configured"])
        self.assertEqual(view["api_key_last4"], "1234")

    def test_reasoning_effort_is_persisted_and_old_updates_preserve_it(self) -> None:
        with db_session() as connection:
            update_model_config(
                connection,
                ConfigUpdate(
                    base_url="https://example.test/v1",
                    model_name="test-model",
                    reasoning_effort="high",
                ),
            )
            update_model_config(
                connection,
                ConfigUpdate(base_url="https://other.test/v1", model_name="other-model"),
            )
            runtime = get_model_runtime_config(connection)
            view = get_model_config_view(connection)

        self.assertEqual(runtime["reasoning_effort"], "high")
        self.assertEqual(view["reasoning_effort"], "high")

    def test_all_reasoning_effort_values_are_persisted(self) -> None:
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort), db_session() as connection:
                view = update_model_config(
                    connection,
                    ConfigUpdate(
                        base_url="https://example.test/v1",
                        model_name="test-model",
                        reasoning_effort=effort,
                    ),
                )

            self.assertEqual(view["reasoning_effort"], effort)

    def test_invalid_persisted_values_are_normalized(self) -> None:
        with db_session() as connection:
            connection.execute(
                "INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?)",
                ("reasoning_effort", "invalid", "now"),
            )
            connection.execute(
                "INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?)",
                ("prompt_max_chars", "120001", "now"),
            )
            view = get_model_config_view(connection)

        self.assertIsNone(view["reasoning_effort"])
        self.assertEqual(view["prompt_max_chars"], 120_000)

    def test_prompt_max_chars_accepts_new_upper_boundary(self) -> None:
        with db_session() as connection:
            view = update_model_config(
                connection,
                ConfigUpdate(
                    base_url="https://example.test/v1",
                    model_name="test-model",
                    prompt_max_chars=120_000,
                ),
            )

        self.assertEqual(view["prompt_max_chars"], 120_000)

    def test_blank_or_null_key_preserves_existing_secret(self) -> None:
        with db_session() as connection:
            update_model_config(
                connection,
                ConfigUpdate(api_key="first-secret", base_url="https://one.test/v1", model_name="one"),
            )
            update_model_config(
                connection,
                ConfigUpdate(api_key="", base_url="https://two.test/v1", model_name="two"),
            )
            self.assertEqual(get_model_runtime_config(connection)["api_key"], "first-secret")
            update_model_config(
                connection,
                ConfigUpdate(api_key=None, base_url="https://three.test/v1", model_name="three"),
            )
            self.assertEqual(get_model_runtime_config(connection)["api_key"], "first-secret")


if __name__ == "__main__":
    unittest.main()
