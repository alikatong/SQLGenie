from __future__ import annotations

import pytest

from backend.evaluation import GoldenQueryCase, evaluate_candidate, evaluate_suite


SCHEMA = {
    "tables": [
        {
            "table_name": "orders",
            "table_comment": "",
            "columns": [
                {"column_name": "id", "data_type": "BIGINT", "column_comment": ""},
                {"column_name": "created_at", "data_type": "TIMESTAMP", "column_comment": ""},
                {"column_name": "amount", "data_type": "DECIMAL", "column_comment": ""},
            ],
        },
        {
            "table_name": "customers",
            "table_comment": "",
            "columns": [
                {"column_name": "id", "data_type": "BIGINT", "column_comment": ""},
                {"column_name": "name", "data_type": "VARCHAR", "column_comment": ""},
            ],
        },
    ]
}


def test_evaluate_candidate_reports_reference_metrics() -> None:
    case = GoldenQueryCase(
        case_id="orders-list",
        question="List orders.id",
        dialect="mysql",
        schema_bundle=SCHEMA,
        expected_tables=("orders",),
        expected_columns=("orders.id",),
    )

    result = evaluate_candidate(case, "SELECT id FROM orders")

    assert result.actual_pass
    assert result.decision_correct
    assert result.table_f1 == 1.0
    assert result.column_f1 == 1.0


def test_suite_counts_semantic_false_accepts_and_error_codes() -> None:
    positive = GoldenQueryCase(
        case_id="amount-total",
        question="Count orders by created_at",
        dialect="mysql",
        schema_bundle=SCHEMA,
        expected_tables=("orders",),
        should_pass=True,
    )
    negative = GoldenQueryCase(
        case_id="unknown-column",
        question="List orders.missing",
        dialect="mysql",
        schema_bundle=SCHEMA,
        should_pass=False,
        accepted_error_codes=("UNKNOWN_COLUMN",),
    )

    results, summary = evaluate_suite(
        [positive, negative],
        {
            "amount-total": "SELECT COUNT(*) FROM orders GROUP BY created_at",
            "unknown-column": "SELECT missing FROM orders",
        },
    )

    assert len(results) == 2
    assert summary.decision_accuracy == 1.0
    assert summary.false_accept_rate == 0.0
    assert summary.false_reject_rate == 0.0
    assert summary.error_code_counts["UNKNOWN_COLUMN"] == 1


def test_suite_requires_a_candidate_for_every_case() -> None:
    case = GoldenQueryCase(
        case_id="missing",
        question="List orders",
        dialect="mysql",
        schema_bundle=SCHEMA,
    )
    with pytest.raises(ValueError, match="missing"):
        evaluate_suite([case], {})
