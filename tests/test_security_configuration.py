from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend.config import settings, validate_security_configuration
from backend.crud import validate_persisted_admin_password
from backend.database import db_session, init_db


class SecurityConfigurationTests(unittest.TestCase):
    def test_rejects_known_credentials_when_listening_on_the_network(self) -> None:
        with (
            patch.object(settings, "app_host", "0.0.0.0"),
            patch.object(settings, "secret_key", "sqlgenie-dev-secret"),
            patch.object(settings, "admin_password", "admin123"),
        ):
            with self.assertRaises(RuntimeError):
                validate_security_configuration()

    def test_allows_strong_credentials_when_listening_on_the_network(self) -> None:
        with (
            patch.object(settings, "app_host", "0.0.0.0"),
            patch.object(settings, "secret_key", "a-long-random-secret"),
            patch.object(settings, "admin_password", "a-long-random-password"),
        ):
            validate_security_configuration()

    def test_rejects_example_placeholder_credentials_on_the_network(self) -> None:
        with (
            patch.object(settings, "app_host", "0.0.0.0"),
            patch.object(settings, "secret_key", "replace-with-a-long-random-secret"),
            patch.object(settings, "admin_password", "replace-with-a-strong-admin-password"),
        ):
            with self.assertRaises(RuntimeError):
                validate_security_configuration()

    def test_rejects_network_start_when_the_stored_admin_keeps_the_default_password(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.db"
            with (
                patch.object(settings, "db_path", database_path),
                patch.object(settings, "app_host", "127.0.0.1"),
                patch.object(settings, "admin_password", "admin123"),
            ):
                init_db()

                with patch.object(settings, "app_host", "0.0.0.0"):
                    with db_session() as connection:
                        with self.assertRaises(RuntimeError):
                            validate_persisted_admin_password(connection)
