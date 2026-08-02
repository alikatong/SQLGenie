from __future__ import annotations

import asyncio

import pytest

from backend.generation import orchestrate_sql_generation
from backend.intent import IntentAnalysis
from backend.llm import ModelCallRecord, ModelCallResult, ModelCandidate
from backend.sql_policy import validate_sql


def _table(name: str, columns: list[tuple[str, str]]) -> dict:
    return {
        "table_name": name,
        "columns": [
            {"column_name": column, "data_type": data_type, "column_comment": ""}
            for column, data_type in columns
        ],
    }


SCHEMA = {
    "tables": [
        _table(
            "visit_record",
            [("id", "INT"), ("department_id", "INT"), ("visit_time", "TIMESTAMP")],
        ),
        _table("department", [("id", "INT"), ("name", "VARCHAR")]),
    ]
}


def test_strict_evidence_rejects_sql_outside_retrieved_tables() -> None:
    result = validate_sql(
        "SELECT id FROM department",
        dialect="mysql",
        schema_bundle=SCHEMA,
        strong_evidence_tables=["visit_record"],
        strict_evidence=True,
    )
    assert not result.passed
    assert result.errors[0].code == "OUTSIDE_RETRIEVED_EVIDENCE"


def test_intent_signals_and_explicit_identifiers_are_checked_against_ast() -> None:
    intent = IntentAnalysis(
        signals=("aggregate", "group_by", "sort", "time_range"),
        explicit_tables=("visit_record",),
        explicit_columns=("visit_record.department_id",),
    )
    missing = validate_sql(
        "SELECT department_id FROM visit_record",
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=intent,
    )
    assert not missing.passed
    assert missing.errors[0].code == "INTENT_AGGREGATE_MISSING"

    complete = validate_sql(
        "SELECT department_id, COUNT(*) AS total FROM visit_record "
        "WHERE visit_time >= '2025-01-01' GROUP BY department_id ORDER BY total DESC",
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=intent,
    )
    assert complete.passed, complete.to_dict()


def test_his_binding_must_be_present_when_concept_is_explicit() -> None:
    intent = IntentAnalysis(his_concepts=("门诊人次",))
    semantics = (
        {
            "term": "门诊人次",
            "synonyms": [],
            "bindings": [{"table": "visit_record", "columns": [], "role": "metric"}],
        },
    )
    result = validate_sql(
        "SELECT id FROM department",
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=intent,
        his_semantics=semantics,
    )
    assert not result.passed
    assert result.errors[0].code == "HIS_BINDING_NOT_USED"


def test_extreme_aggregate_satisfies_highest_request_without_order_by() -> None:
    intent = IntentAnalysis(signals=("aggregate", "sort", "maximum"))
    result = validate_sql(
        "SELECT MAX(department_id) FROM visit_record",
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=intent,
    )
    assert result.passed, result.to_dict()


def test_extreme_aggregate_does_not_satisfy_plain_sort_request() -> None:
    result = validate_sql(
        "SELECT MAX(department_id) FROM visit_record",
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=("sort",)),
    )
    assert not result.passed
    assert result.errors[0].code == "INTENT_SORT_MISSING"


@pytest.mark.parametrize(
    ("signal", "sql", "expected_passed"),
    [
        ("maximum", "SELECT MAX(department_id) FROM visit_record", True),
        ("minimum", "SELECT MIN(department_id) FROM visit_record", True),
        ("maximum", "SELECT id FROM visit_record ORDER BY department_id DESC LIMIT 1", True),
        ("minimum", "SELECT id FROM visit_record ORDER BY department_id ASC LIMIT 1", True),
        ("maximum", "SELECT MIN(department_id) FROM visit_record", False),
        ("minimum", "SELECT MAX(department_id) FROM visit_record", False),
    ],
)
def test_extreme_signal_requires_matching_aggregate_or_single_result_order(
    signal: str,
    sql: str,
    expected_passed: bool,
) -> None:
    result = validate_sql(
        sql,
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=(signal,)),
    )
    assert result.passed is expected_passed, result.to_dict()
    if not expected_passed:
        assert result.errors[0].code == "INTENT_EXTREME_MISSING"


@pytest.mark.parametrize(
    ("signal", "sql", "explicit_columns", "expected_passed"),
    [
        (
            "maximum",
            "SELECT id FROM visit_record ORDER BY id DESC, department_id ASC LIMIT 1",
            ("id",),
            True,
        ),
        (
            "maximum",
            "SELECT id FROM visit_record ORDER BY department_id ASC, id DESC LIMIT 1",
            ("id",),
            False,
        ),
        (
            "latest",
            "SELECT id FROM visit_record ORDER BY visit_time DESC, department_id ASC LIMIT 1",
            (),
            True,
        ),
        (
            "latest",
            "SELECT id FROM visit_record ORDER BY department_id ASC, visit_time DESC LIMIT 1",
            (),
            False,
        ),
        (
            "latest",
            "SELECT id FROM visit_record ORDER BY visit_time DESC LIMIT 1 OFFSET 1",
            (),
            False,
        ),
        (
            "latest",
            "SELECT id FROM visit_record ORDER BY visit_time DESC LIMIT 1 OFFSET 0",
            (),
            False,
        ),
    ],
)
def test_extreme_and_recency_require_global_first_order_key(
    signal: str,
    sql: str,
    explicit_columns: tuple[str, ...],
    expected_passed: bool,
) -> None:
    result = validate_sql(
        sql,
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=(signal,), explicit_columns=explicit_columns),
    )
    assert result.passed is expected_passed, result.to_dict()
    if not expected_passed:
        assert result.errors[0].code in {"INTENT_EXTREME_MISSING", "INTENT_RECENCY_MISSING"}


@pytest.mark.parametrize(
    ("signal", "sql", "expected_passed"),
    [
        ("latest", "SELECT id FROM visit_record ORDER BY visit_time DESC LIMIT 1", True),
        ("first", "SELECT id FROM visit_record ORDER BY visit_time ASC LIMIT 1", True),
        ("latest", "SELECT MAX(visit_time) FROM visit_record", True),
        ("first", "SELECT MIN(visit_time) FROM visit_record", True),
        ("latest", "SELECT id FROM visit_record ORDER BY visit_time ASC LIMIT 1", False),
        ("first", "SELECT id FROM visit_record ORDER BY visit_time DESC LIMIT 1", False),
        ("latest", "SELECT id FROM visit_record ORDER BY department_id DESC LIMIT 1", False),
        ("latest", "SELECT id FROM visit_record ORDER BY visit_time DESC", False),
        ("latest", "SELECT MAX(department_id) FROM visit_record", False),
    ],
)
def test_recency_requires_directional_temporal_single_result_shape(
    signal: str,
    sql: str,
    expected_passed: bool,
) -> None:
    result = validate_sql(
        sql,
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=(signal,)),
    )
    assert result.passed is expected_passed, result.to_dict()
    if not expected_passed:
        assert result.errors[0].code == "INTENT_RECENCY_MISSING"


def test_earliest_explicit_result_field_is_selected_by_time_not_minimum_id() -> None:
    result = validate_sql(
        "SELECT id FROM visit_record ORDER BY visit_time ASC LIMIT 1",
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=("minimum", "first"), explicit_columns=("id",)),
    )
    assert result.passed, result.to_dict()


def test_time_range_is_detected_in_join_on_clause() -> None:
    intent = IntentAnalysis(signals=("time_range",))
    result = validate_sql(
        "SELECT v.id FROM visit_record v JOIN department d "
        "ON d.id = v.department_id AND v.visit_time >= '2025-01-01'",
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=intent,
    )
    assert result.passed, result.to_dict()


@pytest.mark.parametrize(
    ("signal", "sql", "error_code"),
    [
        (
            "aggregate",
            "SELECT id FROM visit_record WHERE id IN (SELECT COUNT(*) FROM department)",
            "INTENT_AGGREGATE_MISSING",
        ),
        (
            "group_by",
            "SELECT id FROM visit_record WHERE id IN (SELECT id FROM department GROUP BY id)",
            "INTENT_GROUP_BY_MISSING",
        ),
        (
            "sort",
            "SELECT id FROM visit_record WHERE EXISTS (SELECT 1 FROM department ORDER BY id DESC)",
            "INTENT_SORT_MISSING",
        ),
        (
            "ranking",
            "SELECT id FROM visit_record WHERE EXISTS (SELECT 1 FROM department ORDER BY id DESC)",
            "INTENT_RANKING_MISSING",
        ),
        (
            "latest",
            "SELECT id FROM visit_record WHERE EXISTS (SELECT 1 FROM department ORDER BY id DESC LIMIT 1)",
            "INTENT_RECENCY_MISSING",
        ),
        (
            "first",
            "SELECT id FROM visit_record WHERE EXISTS (SELECT 1 FROM department ORDER BY id ASC LIMIT 1)",
            "INTENT_RECENCY_MISSING",
        ),
        (
            "time_range",
            "SELECT id FROM visit_record WHERE id IN "
            "(SELECT id FROM visit_record WHERE visit_time >= '2025-01-01')",
            "INTENT_TIME_RANGE_MISSING",
        ),
    ],
)
def test_subquery_cannot_satisfy_outer_query_intent(
    signal: str,
    sql: str,
    error_code: str,
) -> None:
    result = validate_sql(
        sql,
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=(signal,)),
    )
    assert not result.passed
    assert result.errors[0].code == error_code


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM visit_record WHERE visit_time IS NOT NULL",
        "SELECT id FROM visit_record WHERE department_id = 20250101",
        "SELECT id FROM visit_record HAVING MAX(visit_time) >= '2025-01-01'",
        "SELECT v.id FROM visit_record v JOIN visit_record p ON v.visit_time = p.visit_time",
    ],
)
def test_time_range_requires_outer_temporal_filter(sql: str) -> None:
    result = validate_sql(
        sql,
        dialect="mysql",
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=("time_range",)),
    )
    assert not result.passed
    assert result.errors[0].code == "INTENT_TIME_RANGE_MISSING"


@pytest.mark.parametrize(
    ("dialect", "join_sql"),
    [
        (
            "mysql",
            "LEFT JOIN department d ON d.id = v.department_id "
            "AND v.visit_time >= '2025-01-01'",
        ),
        (
            "mysql",
            "RIGHT JOIN department d ON d.id = v.department_id "
            "AND v.visit_time >= '2025-01-01'",
        ),
        (
            "pg",
            "FULL OUTER JOIN department d ON d.id = v.department_id "
            "AND v.visit_time >= '2025-01-01'",
        ),
    ],
)
def test_outer_join_on_temporal_predicate_does_not_satisfy_time_range(
    dialect: str,
    join_sql: str,
) -> None:
    result = validate_sql(
        f"SELECT v.id FROM visit_record v {join_sql}",
        dialect=dialect,
        schema_bundle=SCHEMA,
        intent=IntentAnalysis(signals=("time_range",)),
    )
    assert not result.passed
    assert result.errors[0].code == "INTENT_TIME_RANGE_MISSING"


def test_generation_ignores_unknown_or_weak_schema_evidence() -> None:
    config = {
        "api_key": "unused",
        "base_url": "https://invalid.example/v1",
        "model_name": "test",
        "enable_thinking": True,
        "thinking_timeout_seconds": 30,
    }

    async def forbidden_request(*_args, **_kwargs):
        raise AssertionError("model must not be called")

    import backend.generation as generation

    original = generation.request_model_candidate
    generation.request_model_candidate = forbidden_request
    try:
        for evidence in (
            [{"table_name": "missing_table", "strong_evidence": True, "evidence_score": 1.0}],
            [{"table_name": "visit_record", "evidence_score": 0.2}],
            [{"table_name": "visit_record"}],
        ):
            result = asyncio.run(
                orchestrate_sql_generation(
                    config,
                    target_db_type="mysql",
                    natural_text="查询 visit_record",
                    schema_bundle=SCHEMA,
                    schema_evidence=evidence,
                )
            )
            assert result.sql == "NO_SQL"
            assert result.no_sql_code == "LOW_SCHEMA_EVIDENCE"
    finally:
        generation.request_model_candidate = original


def test_generation_repairs_semantically_incomplete_candidate() -> None:
    config = {
        "api_key": "unused",
        "base_url": "https://invalid.example/v1",
        "model_name": "test",
        "enable_thinking": True,
        "thinking_timeout_seconds": 30,
    }
    calls: list[int] = []

    def record(attempt: int) -> ModelCallRecord:
        return ModelCallRecord(
            stage_name="generation" if attempt == 1 else "repair",
            attempt=attempt,
            model_name="test",
            status_code=200,
            provider_request_id=f"test-{attempt}",
            duration_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
        )

    async def fake_request(*_args, **kwargs):
        attempt = int(kwargs["attempt"])
        calls.append(attempt)
        sql = "SELECT id FROM visit_record" if attempt == 1 else "SELECT COUNT(*) FROM visit_record"
        return ModelCallResult(candidate=ModelCandidate(sql=sql, reason="", assumptions=()), record=record(attempt))

    import backend.generation as generation

    original = generation.request_model_candidate
    generation.request_model_candidate = fake_request
    try:
        result = asyncio.run(
            orchestrate_sql_generation(
                config,
                target_db_type="mysql",
                natural_text="统计 visit_record",
                schema_bundle=SCHEMA,
                schema_evidence=[
                    {"table_name": "visit_record", "strong_evidence": True, "evidence_score": 1.0}
                ],
            )
        )
    finally:
        generation.request_model_candidate = original

    assert calls == [1, 2]
    assert result.sql == "SELECT COUNT(*) FROM visit_record"
    assert result.validation_status == "passed"
