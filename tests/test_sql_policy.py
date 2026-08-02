import pytest

from backend.sql_policy import validate_sql


def _table(name: str, columns: list[str]):
    return {
        "table_name": name,
        "table_comment": "",
        "columns": [
            {"column_name": column, "data_type": "VENDOR_CUSTOM_TYPE", "column_comment": ""}
            for column in columns
        ],
    }


SCHEMA = {
    "tables": [
        _table("visit_record", ["id", "patient_id", "visit_time", "department_id"]),
        _table("department", ["id", "name"]),
        _table("other_visit", ["id", "patient_id"]),
        _table("wide_table", [f"c{i}" for i in range(20)]),
        _table("CaseExact", ["ExactColumn"]),
    ]
}


@pytest.mark.parametrize("dialect", ["mysql", "pg", "oracle"])
def test_all_supported_dialects_accept_scoped_read_query(dialect: str) -> None:
    result = validate_sql(
        "SELECT v.id, d.name FROM visit_record v JOIN department d ON d.id = v.department_id",
        dialect=dialect,
        schema_bundle=SCHEMA,
    )
    assert result.passed
    assert result.tables == ("visit_record", "department")
    assert "visit_record.id" in result.columns


def test_cte_derived_table_and_correlated_subquery_scope() -> None:
    queries = [
        "WITH x AS (SELECT id, patient_id FROM visit_record) SELECT x.id FROM x",
        "SELECT d.id FROM (SELECT id FROM visit_record) d",
        "SELECT v.id FROM visit_record v WHERE EXISTS (SELECT 1 FROM department d WHERE d.id=v.department_id)",
        "SELECT id FROM visit_record UNION SELECT id FROM other_visit",
    ]
    for query in queries:
        assert validate_sql(query, dialect="pg", schema_bundle=SCHEMA).passed, query


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM visit_record", "READ_ONLY_REQUIRED"),
        ("SELECT id FROM visit_record; SELECT id FROM department", "MULTIPLE_STATEMENTS"),
        ("WITH x AS (DELETE FROM visit_record RETURNING id) SELECT * FROM x", "SIDE_EFFECT_STATEMENT"),
        ("SELECT * INTO temp_visit FROM visit_record", "SELECT_INTO"),
        ("SELECT * FROM visit_record FOR UPDATE", "LOCKING_QUERY"),
        ("SELECT @x := id FROM visit_record", "VARIABLE_ASSIGNMENT"),
        ("SELECT PG_READ_FILE('/tmp/x') FROM visit_record", "DANGEROUS_FUNCTION"),
        ("SELECT nope FROM visit_record", "UNKNOWN_COLUMN"),
        ("SELECT id FROM visit_record JOIN department ON visit_record.department_id=department.id", "AMBIGUOUS_COLUMN"),
        ("SELECT x.id FROM missing x", "UNKNOWN_TABLE"),
        ("SELECT q.* FROM visit_record", "UNKNOWN_ALIAS"),
        ("SELECT *", "UNBOUND_STAR"),
    ],
)
def test_rejects_side_effects_and_invalid_scope(sql: str, code: str) -> None:
    result = validate_sql(sql, dialect="pg" if "RETURNING" in sql or "PG_" in sql else "mysql", schema_bundle=SCHEMA)
    assert not result.passed
    assert result.errors[0].code == code


def test_real_comments_rejected_but_comment_markers_inside_string_allowed() -> None:
    rejected = validate_sql("SELECT id FROM visit_record -- comment", dialect="mysql", schema_bundle=SCHEMA)
    assert rejected.errors[0].code == "SQL_COMMENT"
    assert validate_sql("SELECT 'x--y', '/* z */' FROM visit_record", dialect="mysql", schema_bundle=SCHEMA).passed


def test_oracle_database_link_is_rejected() -> None:
    result = validate_sql("SELECT * FROM remote_table@prod", dialect="oracle", schema_bundle=SCHEMA)
    assert result.errors[0].code == "DATABASE_LINK"


def test_oracle_minus_keeps_original_text() -> None:
    sql = "SELECT id FROM visit_record MINUS SELECT id FROM other_visit"
    result = validate_sql(sql, dialect="oracle", schema_bundle=SCHEMA)
    assert result.passed
    assert result.validated_sql == sql
    assert "MINUS" in result.validated_sql


def test_quoted_identifiers_require_exact_case() -> None:
    assert validate_sql('SELECT "ExactColumn" FROM "CaseExact"', dialect="pg", schema_bundle=SCHEMA).passed
    rejected = validate_sql('SELECT "exactcolumn" FROM "CaseExact"', dialect="pg", schema_bundle=SCHEMA)
    assert rejected.errors[0].code == "UNKNOWN_COLUMN"


def test_select_star_and_unfiltered_wide_table_are_warnings() -> None:
    star = validate_sql("SELECT * FROM visit_record", dialect="mysql", schema_bundle=SCHEMA)
    assert star.passed
    assert {warning.code for warning in star.warnings} == {"SELECT_STAR"}

    wide = validate_sql("SELECT c1 FROM wide_table", dialect="mysql", schema_bundle=SCHEMA)
    assert wide.passed
    assert "UNFILTERED_WIDE_TABLE" in {warning.code for warning in wide.warnings}


def test_outside_strong_evidence_is_warning_not_rejection() -> None:
    result = validate_sql(
        "SELECT id FROM department",
        dialect="mysql",
        schema_bundle=SCHEMA,
        strong_evidence_tables=["visit_record"],
    )
    assert result.passed
    assert "OUTSIDE_RETRIEVED_EVIDENCE" in {warning.code for warning in result.warnings}
