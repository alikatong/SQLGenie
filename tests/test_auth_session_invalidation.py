from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.auth import create_access_token, get_current_user
from backend.config import settings
from backend.crud import create_user, delete_user, reset_user_password
from backend.database import db_session, init_db
from backend.schemas import UserCreateRequest, UserPasswordResetRequest


class AuthSessionInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        self.settings_patch = patch.object(settings, "db_path", self.database_path)
        self.settings_patch.start()
        init_db()

    def tearDown(self) -> None:
        self.settings_patch.stop()
        self.temporary_directory.cleanup()

    def _create_user_token(self) -> tuple[int, HTTPAuthorizationCredentials]:
        with db_session() as connection:
            user = create_user(
                connection,
                UserCreateRequest(username="active-user", password="safe-password"),
            )

        token = create_access_token(
            {
                "sub": str(user["id"]),
                "username": user["username"],
                "role": user["role"],
                "token_version": str(user["token_version"]),
            }
        )
        return user["id"], HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def test_deleted_user_token_is_rejected(self) -> None:
        user_id, credentials = self._create_user_token()
        self.assertEqual(get_current_user(credentials).id, user_id)

        with db_session() as connection:
            self.assertTrue(delete_user(connection, user_id))

        with self.assertRaises(HTTPException) as error:
            get_current_user(credentials)

        self.assertEqual(error.exception.status_code, 401)

    def test_password_reset_invalidates_existing_token(self) -> None:
        user_id, credentials = self._create_user_token()
        self.assertEqual(get_current_user(credentials).id, user_id)

        with db_session() as connection:
            reset_user_password(
                connection,
                user_id,
                UserPasswordResetRequest(password="new-safe-password"),
            )

        with self.assertRaises(HTTPException) as error:
            get_current_user(credentials)

        self.assertEqual(error.exception.status_code, 401)
