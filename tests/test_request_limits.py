from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.schemas import (
    GenerateSqlRequest,
    TableUpload,
    TableUploadRequest,
    UserCreateRequest,
    UserPasswordResetRequest,
)


class RequestLimitTests(unittest.TestCase):
    def test_rejects_an_oversized_sql_question(self) -> None:
        with self.assertRaises(ValidationError):
            GenerateSqlRequest(
                db_id=1,
                natural_text="x" * 4001,
                target_db_type="mysql",
            )

    def test_rejects_schema_with_too_many_tables(self) -> None:
        table = TableUpload(table_name="orders")

        with self.assertRaises(ValidationError):
            TableUploadRequest(tables=[table] * 101)

    def test_rejects_multibyte_password_above_bcrypt_byte_limit(self) -> None:
        with self.assertRaises(ValidationError):
            UserCreateRequest(username="user", password="汉" * 25, role="user")
        with self.assertRaises(ValidationError):
            UserPasswordResetRequest(password="汉" * 25)

    def test_accepts_password_at_exactly_seventy_two_utf8_bytes(self) -> None:
        UserCreateRequest(username="user", password="汉" * 24, role="user")
        UserPasswordResetRequest(password="汉" * 24)
