from __future__ import annotations

import unittest

from sqlglot import exp

from backend.sql_policy import _side_effect_issue, validate_sql


class SqlPolicyAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = {
            "tables": [
                {
                    "table_name": "patients",
                    "columns": [
                        {"column_name": "id", "data_type": "NUMBER"},
                        {"column_name": "name", "data_type": "VENDOR_TEXT"},
                        {"column_name": "dept_id", "data_type": "NUMBER"},
                    ],
                },
                {
                    "table_name": "departments",
                    "columns": [
                        {"column_name": "id", "data_type": "NUMBER"},
                        {"column_name": "name", "data_type": "VENDOR_TEXT"},
                    ],
                },
            ]
        }

    def assert_rejected(self, sql: str, dialect: str, *codes: str) -> None:
        result = validate_sql(sql, dialect=dialect, schema_bundle=self.schema)
        self.assertEqual(result.status, "failed", result.to_dict())
        self.assertTrue(set(codes).intersection(issue.code for issue in result.errors), result.to_dict())
        self.assertEqual(result.validated_sql, "")

    def test_postgres_data_modifying_ctes_are_rejected_at_any_depth(self) -> None:
        cases = (
            "WITH changed AS (DELETE FROM patients RETURNING id) SELECT id FROM changed",
            "WITH changed AS (UPDATE patients SET name = 'x' RETURNING id) SELECT id FROM changed",
            "WITH changed AS (INSERT INTO patients(id) VALUES (1) RETURNING id) SELECT id FROM changed",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, "pg", "SIDE_EFFECT_STATEMENT", "READ_ONLY_REQUIRED")

    def test_side_effect_select_forms_are_rejected(self) -> None:
        cases = (
            ("pg", "SELECT id INTO TEMP TABLE stolen FROM patients", "SELECT_INTO"),
            ("pg", "SELECT id FROM patients FOR UPDATE", "LOCKING_QUERY"),
            ("mysql", "SELECT @captured := id FROM patients", "VARIABLE_ASSIGNMENT"),
            (
                "mysql",
                "SELECT id INTO OUTFILE '/tmp/result' FROM patients",
                ("SELECT_INTO", "SQL_PARSE_ERROR"),
            ),
        )
        for dialect, sql, codes in cases:
            with self.subTest(dialect=dialect, sql=sql):
                accepted_codes = codes if isinstance(codes, tuple) else (codes,)
                self.assert_rejected(sql, dialect, *accepted_codes)

    def test_remote_access_and_side_effect_functions_are_rejected(self) -> None:
        cases = (
            ("oracle", "SELECT id FROM patients@remote_his", "DATABASE_LINK"),
            ("oracle", "SELECT UTL_HTTP.REQUEST(name) FROM patients", "DANGEROUS_FUNCTION"),
            ("oracle", "SELECT patient_seq.NEXTVAL FROM dual", "DANGEROUS_FUNCTION"),
            ("pg", "SELECT PG_READ_FILE(name) FROM patients", "DANGEROUS_FUNCTION"),
            ("pg", "SELECT PG_ADVISORY_LOCK(id) FROM patients", "DANGEROUS_FUNCTION"),
            ("mysql", "SELECT LOAD_FILE(name) FROM patients", "DANGEROUS_FUNCTION"),
            ("mysql", "SELECT SLEEP(id) FROM patients", "DANGEROUS_FUNCTION"),
        )
        for dialect, sql, code in cases:
            with self.subTest(dialect=dialect, sql=sql):
                self.assert_rejected(sql, dialect, code)

    def test_side_effect_functions_cannot_bypass_the_denylist(self) -> None:
        cases = (
            ("pg", "SELECT pg_terminate_backend(1) FROM patients"),
            ("pg", "SELECT lo_import('/etc/passwd') FROM patients"),
            ("mysql", "SELECT sys_exec('id') FROM patients"),
            ("oracle", "SELECT DBMS_PIPE.RECEIVE_MESSAGE('x', 1) FROM patients"),
        )
        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                self.assert_rejected(sql, dialect, "DANGEROUS_FUNCTION")

    def test_next_value_for_ast_is_rejected(self) -> None:
        node = exp.Select(
            expressions=[exp.NextValueFor(this=exp.to_identifier("patient_seq"))],
        )
        issue = _side_effect_issue(node)
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertEqual(issue.code, "DANGEROUS_FUNCTION")

    def test_unverified_anonymous_and_package_functions_fail_closed(self) -> None:
        cases = (
            ("pg", "SELECT custom_safe(id) FROM patients"),
            ("oracle", "SELECT custom_pkg.read_value(id) FROM patients"),
        )
        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                self.assert_rejected(sql, dialect, "UNVERIFIED_FUNCTION")

    def test_parser_recognized_read_only_functions_still_pass(self) -> None:
        cases = (
            ("pg", "SELECT COUNT(*), COALESCE(name, '') FROM patients"),
            ("mysql", "SELECT COUNT(*), DATE_FORMAT(CURRENT_TIMESTAMP, '%Y-%m-%d') FROM patients"),
            ("oracle", "SELECT COUNT(*), NVL(name, ''), TO_CHAR(SYSDATE, 'YYYY-MM-DD') FROM patients"),
        )
        for dialect, sql in cases:
            with self.subTest(dialect=dialect, sql=sql):
                result = validate_sql(sql, dialect=dialect, schema_bundle=self.schema)
                self.assertEqual(result.status, "passed", result.to_dict())

    def test_oracle_minus_passes_without_transpiling_original_text(self) -> None:
        sql = "SELECT id FROM patients MINUS SELECT id FROM patients"
        result = validate_sql(sql, dialect="oracle", schema_bundle=self.schema)
        self.assertEqual(result.status, "passed", result.to_dict())
        self.assertEqual(result.validated_sql, sql)
        self.assertNotIn("EXCEPT", result.validated_sql)

    def test_comment_markers_inside_strings_are_data_but_real_comments_fail(self) -> None:
        for marker in ("-- not a comment", "/* not a comment */"):
            sql = f"SELECT '{marker}' AS marker, id FROM patients"
            with self.subTest(marker=marker):
                result = validate_sql(sql, dialect="mysql", schema_bundle=self.schema)
                self.assertEqual(result.status, "passed", result.to_dict())
                self.assertEqual(result.validated_sql, sql)

        for sql in (
            "SELECT id FROM patients -- real comment",
            "SELECT /* real comment */ id FROM patients",
        ):
            with self.subTest(sql=sql):
                self.assert_rejected(sql, "mysql", "SQL_COMMENT")

    def test_unknown_and_ambiguous_columns_never_pass(self) -> None:
        cases = (
            ("SELECT missing FROM patients", "UNKNOWN_COLUMN"),
            ("SELECT ghost.id FROM patients p", "UNKNOWN_ALIAS"),
            (
                "SELECT id FROM patients p JOIN departments d ON p.dept_id = d.id",
                "AMBIGUOUS_COLUMN",
            ),
            ("WITH q AS (SELECT id FROM patients) SELECT q.missing FROM q", "UNKNOWN_COLUMN"),
            ("SELECT d.missing FROM (SELECT id FROM patients) d", "UNKNOWN_COLUMN"),
        )
        for sql, code in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, "mysql", code)

    def test_cte_derived_and_correlated_scopes_resolve_without_fake_tables(self) -> None:
        cases = (
            "WITH p AS (SELECT id FROM patients) SELECT p.id FROM p",
            "SELECT q.id FROM (SELECT id FROM patients) q",
            (
                "SELECT p.id FROM patients p "
                "WHERE EXISTS (SELECT 1 FROM departments d WHERE d.id = p.dept_id)"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                result = validate_sql(sql, dialect="mysql", schema_bundle=self.schema)
                self.assertEqual(result.status, "passed", result.to_dict())
                self.assertNotIn("p", result.tables)
                self.assertNotIn("q", result.tables)

    def test_exact_quoted_identifiers_are_valid_and_case_mismatch_is_not(self) -> None:
        quoted_schema = {
            "tables": [
                {
                    "table_name": "PatientCase",
                    "columns": [{"column_name": "ID", "data_type": "VENDOR_NUMBER"}],
                }
            ]
        }
        exact = validate_sql(
            'SELECT "ID" FROM "PatientCase"',
            dialect="pg",
            schema_bundle=quoted_schema,
        )
        mismatch = validate_sql(
            'SELECT "id" FROM "PatientCase"',
            dialect="pg",
            schema_bundle=quoted_schema,
        )
        self.assertEqual(exact.status, "passed", exact.to_dict())
        self.assertEqual(exact.columns, ("PatientCase.ID",))
        self.assertEqual(mismatch.status, "failed", mismatch.to_dict())
        self.assertIn("UNKNOWN_COLUMN", {issue.code for issue in mismatch.errors})


if __name__ == "__main__":
    unittest.main()
