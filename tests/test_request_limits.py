from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.schemas import GenerateSqlRequest, TableUpload, TableUploadRequest


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
