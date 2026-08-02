from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Mapping, Sequence

from .intent import analyze_intent
from .sql_policy import SqlValidationResult, validate_sql


@dataclass(frozen=True)
class GoldenQueryCase:
    """A deterministic accuracy case that never executes target SQL."""

    case_id: str
    question: str
    dialect: str
    schema_bundle: Mapping[str, Any]
    expected_tables: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = ()
    should_pass: bool = True
    accepted_error_codes: tuple[str, ...] = ()
    his_semantics: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    expected_pass: bool
    actual_pass: bool
    decision_correct: bool
    error_codes: tuple[str, ...]
    referenced_tables: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    table_precision: float
    table_recall: float
    table_f1: float
    column_precision: float
    column_recall: float
    column_f1: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationSummary:
    cases: int
    decision_accuracy: float
    policy_pass_rate: float
    false_accept_rate: float
    false_reject_rate: float
    table_exact_match_rate: float
    column_exact_match_rate: float
    mean_table_f1: float
    mean_column_f1: float
    error_code_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["error_code_counts"] = dict(self.error_code_counts)
        return value


def _canonical(values: Sequence[str]) -> set[str]:
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _precision_recall_f1(expected: set[str], actual: set[str]) -> tuple[float, float, float]:
    if not expected and not actual:
        return 1.0, 1.0, 1.0
    overlap = len(expected & actual)
    precision = overlap / len(actual) if actual else 0.0
    recall = overlap / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _decision_correct(case: GoldenQueryCase, validation: SqlValidationResult) -> bool:
    if case.should_pass:
        return validation.passed
    if validation.passed:
        return False
    if not case.accepted_error_codes:
        return True
    codes = {issue.code for issue in validation.errors}
    return bool(codes & set(case.accepted_error_codes))


def evaluate_candidate(case: GoldenQueryCase, sql: str) -> CaseEvaluation:
    """Evaluate one candidate with the same local policy used in production."""

    intent = analyze_intent(
        case.question,
        schema_bundle=case.schema_bundle,
        his_semantics=case.his_semantics,
    )
    validation = validate_sql(
        sql,
        dialect=case.dialect,
        schema_bundle=case.schema_bundle,
        intent=intent,
        his_semantics=case.his_semantics,
    )

    expected_tables = _canonical(case.expected_tables)
    actual_tables = _canonical(validation.tables)
    expected_columns = _canonical(case.expected_columns)
    actual_columns = _canonical(validation.columns)
    table_precision, table_recall, table_f1 = _precision_recall_f1(expected_tables, actual_tables)
    column_precision, column_recall, column_f1 = _precision_recall_f1(expected_columns, actual_columns)

    return CaseEvaluation(
        case_id=case.case_id,
        expected_pass=case.should_pass,
        actual_pass=validation.passed,
        decision_correct=_decision_correct(case, validation),
        error_codes=tuple(issue.code for issue in validation.errors),
        referenced_tables=validation.tables,
        referenced_columns=validation.columns,
        table_precision=table_precision,
        table_recall=table_recall,
        table_f1=table_f1,
        column_precision=column_precision,
        column_recall=column_recall,
        column_f1=column_f1,
    )


def summarize_evaluations(results: Sequence[CaseEvaluation]) -> EvaluationSummary:
    if not results:
        return EvaluationSummary(
            cases=0,
            decision_accuracy=0.0,
            policy_pass_rate=0.0,
            false_accept_rate=0.0,
            false_reject_rate=0.0,
            table_exact_match_rate=0.0,
            column_exact_match_rate=0.0,
            mean_table_f1=0.0,
            mean_column_f1=0.0,
            error_code_counts={},
        )

    false_accepts = sum(not item.expected_pass and item.actual_pass for item in results)
    false_rejects = sum(item.expected_pass and not item.actual_pass for item in results)
    negative_cases = sum(not item.expected_pass for item in results)
    positive_cases = sum(item.expected_pass for item in results)
    error_counts: dict[str, int] = {}
    for item in results:
        for code in item.error_codes:
            error_counts[code] = error_counts.get(code, 0) + 1

    return EvaluationSummary(
        cases=len(results),
        decision_accuracy=sum(item.decision_correct for item in results) / len(results),
        policy_pass_rate=sum(item.actual_pass for item in results) / len(results),
        false_accept_rate=false_accepts / negative_cases if negative_cases else 0.0,
        false_reject_rate=false_rejects / positive_cases if positive_cases else 0.0,
        table_exact_match_rate=sum(item.table_f1 == 1.0 for item in results) / len(results),
        column_exact_match_rate=sum(item.column_f1 == 1.0 for item in results) / len(results),
        mean_table_f1=fmean(item.table_f1 for item in results),
        mean_column_f1=fmean(item.column_f1 for item in results),
        error_code_counts=error_counts,
    )


def evaluate_suite(
    cases: Sequence[GoldenQueryCase],
    candidates: Mapping[str, str],
) -> tuple[list[CaseEvaluation], EvaluationSummary]:
    missing = [case.case_id for case in cases if case.case_id not in candidates]
    if missing:
        raise ValueError(f"Missing SQL candidates for cases: {', '.join(missing)}")
    results = [evaluate_candidate(case, candidates[case.case_id]) for case in cases]
    return results, summarize_evaluations(results)


__all__ = [
    "CaseEvaluation",
    "EvaluationSummary",
    "GoldenQueryCase",
    "evaluate_candidate",
    "evaluate_suite",
    "summarize_evaluations",
]
