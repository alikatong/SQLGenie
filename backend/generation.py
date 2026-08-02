from __future__ import annotations

from dataclasses import asdict, dataclass
from time import monotonic
from typing import Any, Iterable, Mapping, Sequence

from .config import settings
from .intent import IntentAnalysis, analyze_intent
from .llm import (
    ModelCallObserver,
    ModelCallRecord,
    ModelCandidate,
    ModelContractError,
    ModelGatewayError,
    request_model_candidate,
)
from .prompting import (
    MODEL_MESSAGE_MAX_CHARS,
    PROMPT_MAX_CHARS,
    PROMPT_VERSION,
    PromptPackage,
    PromptTooLargeError,
    compile_generation_prompt,
    compile_repair_prompt,
)
from .sql_policy import POLICY_VERSION, SqlValidationResult, ValidationIssue, validate_sql


@dataclass(frozen=True)
class GenerationResult:
    sql: str
    reason: str
    assumptions: tuple[str, ...]
    validation_status: str
    validation_errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    prompt_version: str
    policy_version: str
    context_hash: str
    model_calls: int
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int
    prompt_chars: int
    no_sql_code: str
    call_records: tuple[ModelCallRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "reason": self.reason,
            "assumptions": list(self.assumptions),
            "validation_status": self.validation_status,
            "validation_errors": [item.to_dict() for item in self.validation_errors],
            "warnings": [item.to_dict() for item in self.warnings],
            "tables": list(self.tables),
            "columns": list(self.columns),
            "prompt_version": self.prompt_version,
            "policy_version": self.policy_version,
            "context_hash": self.context_hash,
            "model_calls": self.model_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "duration_ms": self.duration_ms,
            "prompt_chars": self.prompt_chars,
            "no_sql_code": self.no_sql_code,
            "call_records": [record.to_dict() for record in self.call_records],
        }


class GenerationError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        model_calls: int = 0,
        call_records: Sequence[ModelCallRecord] = (),
        prompt_version: str = PROMPT_VERSION,
        policy_version: str = POLICY_VERSION,
        context_hash: str = "",
        prompt_chars: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        duration_ms: int = 0,
        validation_status: str = "not_run",
        validation_errors: Sequence[ValidationIssue] = (),
        warnings: Sequence[ValidationIssue] = (),
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.model_calls = model_calls
        self.call_records = tuple(call_records)
        self.prompt_version = prompt_version
        self.policy_version = policy_version
        self.context_hash = context_hash
        self.prompt_chars = prompt_chars
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.duration_ms = duration_ms
        self.validation_status = validation_status
        self.validation_errors = tuple(validation_errors)
        self.warnings = tuple(warnings)


def _sum_usage(records: Sequence[ModelCallRecord], name: str) -> int | None:
    values = [getattr(record, name) for record in records if getattr(record, name) is not None]
    return sum(values) if values else None


def _elapsed_ms(started_at: float) -> int:
    return max(int((monotonic() - started_at) * 1000), 0)


def _remaining_seconds(started_at: float, total_timeout_seconds: int) -> float:
    remaining = float(total_timeout_seconds) - (monotonic() - started_at)
    if remaining <= 0:
        raise ModelGatewayError(
            status_code=504,
            error_code="MODEL_TIMEOUT",
            message=f"大模型请求达到 {total_timeout_seconds} 秒总等待上限。",
        )
    return max(remaining, 0.001)


def _intent_warnings(intent: IntentAnalysis) -> tuple[ValidationIssue, ...]:
    return tuple(ValidationIssue(item.code, item.message) for item in intent.warnings)


def _evidence_table_name(value: Mapping[str, Any]) -> str:
    return str(value.get("table_name", value.get("table", ""))).strip()


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_strong_evidence(value: Mapping[str, Any], schema_tables: set[str]) -> bool:
    table_name = _evidence_table_name(value)
    if not table_name or table_name.casefold() not in schema_tables:
        return False
    if value.get("expanded_from") is not None:
        return False
    if "strong_evidence" in value:
        if not bool(value.get("strong_evidence")):
            return False
        # Compatibility for typed callers: an explicit strong marker still
        # needs a meaningful score, so a weak/empty object cannot unlock a call.
        return _float_value(value.get("evidence_score")) >= 0.8
    if "is_strong" in value:
        return bool(value.get("is_strong")) and _float_value(value.get("evidence_score")) >= 0.8

    reasons = {str(item).casefold() for item in value.get("reasons", ()) or ()}
    if "explicit" in reasons:
        return True
    if "his_term" in reasons:
        return _float_value(value.get("evidence_score")) >= 0.8
    if "keyword" in reasons and _float_value(value.get("keyword_score")) >= settings.rag_min_keyword_score:
        return True
    if "vector" in reasons:
        return (
            _float_value(value.get("vector_similarity"), -1.0) >= settings.rag_min_vector_similarity
            and _float_value(value.get("vector_margin"), -1.0) >= settings.rag_min_vector_margin
        )
    return False


def _hydrate_schema_evidence(
    schema_bundle: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_name = {
        str(table.get("table_name", "")).casefold(): dict(table)
        for table in schema_bundle.get("tables", ()) or ()
        if isinstance(table, Mapping) and str(table.get("table_name", "")).strip()
    }
    hydrated: list[dict[str, Any]] = []
    for item in evidence:
        name = _evidence_table_name(item)
        table = by_name.get(name.casefold())
        if table is None:
            continue
        value = dict(item)
        # Schema fields always come from the current local bundle. Evidence is
        # ranking metadata and may not override table identity or columns.
        value["table_name"] = str(table.get("table_name", name))
        value["table_comment"] = str(table.get("table_comment", ""))
        value["columns"] = list(table.get("columns", ()) or ())
        hydrated.append(value)
    return hydrated


def _local_no_sql(
    *,
    reason: str,
    no_sql_code: str,
    started_at: float,
    intent: IntentAnalysis,
    prompt_chars: int = 0,
) -> GenerationResult:
    return GenerationResult(
        sql="NO_SQL",
        reason=reason,
        assumptions=(),
        validation_status="not_run",
        validation_errors=(),
        warnings=_intent_warnings(intent),
        tables=(),
        columns=(),
        prompt_version=PROMPT_VERSION,
        policy_version=POLICY_VERSION,
        context_hash="",
        model_calls=0,
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=_elapsed_ms(started_at),
        prompt_chars=prompt_chars,
        no_sql_code=no_sql_code,
        call_records=(),
    )


def _result_from_validation(
    *,
    validation: SqlValidationResult,
    candidate: ModelCandidate,
    started_at: float,
    prompt: PromptPackage,
    prompt_chars: int,
    records: Sequence[ModelCallRecord],
    intent: IntentAnalysis,
    no_sql_code: str = "",
    reason: str = "",
) -> GenerationResult:
    warnings = list(_intent_warnings(intent))
    warnings.extend(validation.warnings)
    return GenerationResult(
        sql=validation.validated_sql if validation.passed else "NO_SQL",
        reason=reason,
        assumptions=candidate.assumptions,
        validation_status=validation.status,
        validation_errors=validation.errors,
        warnings=tuple(warnings),
        tables=validation.tables,
        columns=validation.columns,
        prompt_version=prompt.prompt_version,
        policy_version=validation.policy_version,
        context_hash=prompt.context_hash,
        model_calls=len(records),
        prompt_tokens=_sum_usage(records, "prompt_tokens"),
        completion_tokens=_sum_usage(records, "completion_tokens"),
        duration_ms=_elapsed_ms(started_at),
        prompt_chars=prompt_chars,
        no_sql_code=no_sql_code,
        call_records=tuple(records),
    )


def _raise_generation_error(
    exc: ModelGatewayError,
    *,
    started_at: float,
    records: list[ModelCallRecord],
    prompt: PromptPackage | None,
    prompt_chars: int,
    validation: SqlValidationResult | None = None,
    intent: IntentAnalysis | None = None,
) -> None:
    if exc.record is not None and exc.record not in records:
        records.append(exc.record)
    raise GenerationError(
        status_code=exc.status_code,
        error_code=exc.error_code,
        message=exc.message,
        model_calls=len(records),
        call_records=records,
        prompt_version=prompt.prompt_version if prompt is not None else PROMPT_VERSION,
        policy_version=validation.policy_version if validation is not None else POLICY_VERSION,
        context_hash=prompt.context_hash if prompt else "",
        prompt_chars=prompt_chars,
        prompt_tokens=_sum_usage(records, "prompt_tokens"),
        completion_tokens=_sum_usage(records, "completion_tokens"),
        duration_ms=_elapsed_ms(started_at),
        validation_status=validation.status if validation is not None else "not_run",
        validation_errors=validation.errors if validation is not None else (),
        warnings=(
            (_intent_warnings(intent) if intent is not None else ())
            + (validation.warnings if validation is not None else ())
        ),
    ) from exc


async def orchestrate_sql_generation(
    model_config: Mapping[str, Any],
    *,
    target_db_type: str,
    natural_text: str,
    schema_bundle: Mapping[str, Any],
    schema_evidence: Sequence[Mapping[str, Any]] = (),
    his_semantics: Sequence[Mapping[str, Any]] = (),
    verified_examples: Sequence[Mapping[str, Any]] = (),
    intent_result: IntentAnalysis | None = None,
    call_observer: ModelCallObserver | None = None,
    request_started_at: float | None = None,
) -> GenerationResult:
    """One generation call first; at most one repair after local failure."""

    started_at = request_started_at if request_started_at is not None else monotonic()
    records: list[ModelCallRecord] = []
    prompt: PromptPackage | None = None
    total_prompt_chars = 0
    total_timeout = min(max(int(model_config.get("thinking_timeout_seconds", 600)), 10), 600)
    prompt_max_chars = min(
        max(int(model_config.get("prompt_max_chars", MODEL_MESSAGE_MAX_CHARS)), 1_000),
        PROMPT_MAX_CHARS,
    )
    try:
        _remaining_seconds(started_at, total_timeout)
    except ModelGatewayError as exc:
        _raise_generation_error(
            exc,
            started_at=started_at,
            records=records,
            prompt=None,
            prompt_chars=total_prompt_chars,
        )
    intent = intent_result or analyze_intent(
        natural_text,
        schema_bundle=schema_bundle,
        his_semantics=his_semantics,
    )
    if intent.error_code:
        raise GenerationError(
            status_code=422,
            error_code=intent.error_code,
            message=intent.clarification_reason,
            duration_ms=_elapsed_ms(started_at),
        )
    if intent.requires_clarification:
        return _local_no_sql(
            reason=intent.clarification_reason,
            no_sql_code="LOW_SCHEMA_EVIDENCE",
            started_at=started_at,
            intent=intent,
        )

    hydrated_evidence = _hydrate_schema_evidence(schema_bundle, schema_evidence)
    schema_table_names = {
        str(table.get("table_name", "")).casefold()
        for table in schema_bundle.get("tables", ()) or ()
        if isinstance(table, Mapping) and str(table.get("table_name", "")).strip()
    }
    strong_tables = list(dict.fromkeys(
        _evidence_table_name(item)
        for item in hydrated_evidence
        if _is_strong_evidence(item, schema_table_names) and _evidence_table_name(item)
    ))
    if not strong_tables:
        return _local_no_sql(
            reason="没有达到强证据门槛的 Schema 表；请明确表、字段或补充 HIS 术语绑定。",
            no_sql_code="LOW_SCHEMA_EVIDENCE",
            started_at=started_at,
            intent=intent,
        )

    try:
        prompt = compile_generation_prompt(
            dialect=target_db_type,
            intent=intent,
            his_semantics=his_semantics,
            schema_evidence=hydrated_evidence,
            verified_examples=verified_examples,
            user_request=natural_text,
            max_chars=prompt_max_chars,
        )
    except PromptTooLargeError as exc:
        return _local_no_sql(
            reason=str(exc),
            no_sql_code="CONTEXT_TOO_LARGE",
            started_at=started_at,
            intent=intent,
            prompt_chars=exc.prompt_chars,
        )
    total_prompt_chars = prompt.prompt_chars
    repair_enabled = bool(model_config.get("enable_thinking", True))

    first_candidate: ModelCandidate | None = None
    first_contract_error: ModelContractError | None = None
    try:
        first_call = await request_model_candidate(
            model_config,
            messages=prompt.messages,
            stage_name="SQL 生成",
            attempt=1,
            request_timeout_seconds=_remaining_seconds(started_at, total_timeout),
            call_observer=call_observer,
        )
        records.append(first_call.record)
        first_candidate = first_call.candidate
    except ModelContractError as exc:
        if exc.record is not None:
            records.append(exc.record)
        first_contract_error = exc
        if not repair_enabled:
            _raise_generation_error(
                exc,
                started_at=started_at,
                records=records,
                prompt=prompt,
                prompt_chars=total_prompt_chars,
                intent=intent,
            )
    except ModelGatewayError as exc:
        _raise_generation_error(
            exc,
            started_at=started_at,
            records=records,
            prompt=prompt,
            prompt_chars=total_prompt_chars,
            intent=intent,
        )

    if first_candidate is not None and first_candidate.sql == "NO_SQL":
        return GenerationResult(
            sql="NO_SQL",
            reason=first_candidate.reason,
            assumptions=first_candidate.assumptions,
            validation_status="not_run",
            validation_errors=(),
            warnings=_intent_warnings(intent),
            tables=(),
            columns=(),
            prompt_version=prompt.prompt_version,
            policy_version=POLICY_VERSION,
            context_hash=prompt.context_hash,
            model_calls=len(records),
            prompt_tokens=_sum_usage(records, "prompt_tokens"),
            completion_tokens=_sum_usage(records, "completion_tokens"),
            duration_ms=_elapsed_ms(started_at),
            prompt_chars=total_prompt_chars,
            no_sql_code="MODEL_DECLINED",
            call_records=tuple(records),
        )

    first_validation: SqlValidationResult | None = None
    if first_candidate is not None:
        first_validation = validate_sql(
            first_candidate.sql,
            dialect=target_db_type,
            schema_bundle=schema_bundle,
            strong_evidence_tables=strong_tables,
            intent=intent,
            his_semantics=his_semantics,
            strict_evidence=True,
        )
        if first_validation.passed:
            return _result_from_validation(
                validation=first_validation,
                candidate=first_candidate,
                started_at=started_at,
                prompt=prompt,
                prompt_chars=total_prompt_chars,
                records=records,
                intent=intent,
            )
        if not repair_enabled:
            return _result_from_validation(
                validation=first_validation,
                candidate=first_candidate,
                started_at=started_at,
                prompt=prompt,
                prompt_chars=total_prompt_chars,
                records=records,
                intent=intent,
                no_sql_code="VALIDATION_FAILED",
                reason="候选 SQL 未通过本地策略校验。",
            )

    repair_errors: list[dict[str, str]]
    repair_candidate_output: Any
    if first_contract_error is not None:
        repair_errors = [{"code": first_contract_error.error_code, "message": first_contract_error.message}]
        repair_candidate_output = first_contract_error.candidate_output or {"invalid_response": True}
    else:
        assert first_candidate is not None and first_validation is not None
        repair_errors = [issue.to_dict() for issue in first_validation.errors]
        repair_candidate_output = first_candidate.to_dict()

    try:
        repair_prompt = compile_repair_prompt(
            prompt,
            candidate_output=repair_candidate_output,
            validation_errors=repair_errors,
            max_chars=prompt_max_chars,
        )
    except PromptTooLargeError:
        if first_validation is not None and first_candidate is not None:
            return _result_from_validation(
                validation=first_validation,
                candidate=first_candidate,
                started_at=started_at,
                prompt=prompt,
                prompt_chars=total_prompt_chars,
                records=records,
                intent=intent,
                no_sql_code="CONTEXT_TOO_LARGE",
                reason="修复上下文超过字符上限，未发起第二次模型调用。",
            )
        raise GenerationError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="首次响应不符合 JSON 契约，且修复上下文超过字符上限。",
            model_calls=len(records),
            call_records=records,
            context_hash=prompt.context_hash,
            prompt_chars=total_prompt_chars,
            prompt_tokens=_sum_usage(records, "prompt_tokens"),
            completion_tokens=_sum_usage(records, "completion_tokens"),
            duration_ms=_elapsed_ms(started_at),
        )

    total_prompt_chars += repair_prompt.prompt_chars
    try:
        repair_call = await request_model_candidate(
            model_config,
            messages=repair_prompt.messages,
            stage_name="SQL 修复",
            attempt=2,
            request_timeout_seconds=_remaining_seconds(started_at, total_timeout),
            call_observer=call_observer,
        )
        records.append(repair_call.record)
    except ModelGatewayError as exc:
        _raise_generation_error(
            exc,
            started_at=started_at,
            records=records,
            prompt=prompt,
            prompt_chars=total_prompt_chars,
            validation=first_validation,
            intent=intent,
        )

    repair_candidate = repair_call.candidate
    if repair_candidate.sql == "NO_SQL":
        validation_errors = first_validation.errors if first_validation is not None else (
            ValidationIssue("MODEL_RESPONSE_INVALID", "首次响应不符合严格 JSON 契约。"),
        )
        return GenerationResult(
            sql="NO_SQL",
            reason=repair_candidate.reason,
            assumptions=repair_candidate.assumptions,
            validation_status="failed",
            validation_errors=tuple(validation_errors),
            warnings=_intent_warnings(intent),
            tables=first_validation.tables if first_validation is not None else (),
            columns=first_validation.columns if first_validation is not None else (),
            prompt_version=prompt.prompt_version,
            policy_version=POLICY_VERSION,
            context_hash=prompt.context_hash,
            model_calls=len(records),
            prompt_tokens=_sum_usage(records, "prompt_tokens"),
            completion_tokens=_sum_usage(records, "completion_tokens"),
            duration_ms=_elapsed_ms(started_at),
            prompt_chars=total_prompt_chars,
            no_sql_code="VALIDATION_FAILED",
            call_records=tuple(records),
        )

    repair_validation = validate_sql(
        repair_candidate.sql,
        dialect=target_db_type,
        schema_bundle=schema_bundle,
        strong_evidence_tables=strong_tables,
        intent=intent,
        his_semantics=his_semantics,
        strict_evidence=True,
    )
    if repair_validation.passed:
        return _result_from_validation(
            validation=repair_validation,
            candidate=repair_candidate,
            started_at=started_at,
            prompt=prompt,
            prompt_chars=total_prompt_chars,
            records=records,
            intent=intent,
        )
    return _result_from_validation(
        validation=repair_validation,
        candidate=repair_candidate,
        started_at=started_at,
        prompt=prompt,
        prompt_chars=total_prompt_chars,
        records=records,
        intent=intent,
        no_sql_code="VALIDATION_FAILED",
        reason="修复后的 SQL 仍未通过本地策略校验。",
    )


generate_sql = orchestrate_sql_generation


__all__ = ["GenerationError", "GenerationResult", "generate_sql", "orchestrate_sql_generation"]
