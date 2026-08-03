import asyncio

import pytest

from backend.generation import GenerationError, orchestrate_sql_generation
from backend.llm import ModelCallRecord, ModelCallResult, ModelCandidate, ModelContractError


SCHEMA = {
    "tables": [
        {
            "table_name": "visit_record",
            "table_comment": "visits",
            "columns": [
                {"column_name": "id", "data_type": "BIGINT", "column_comment": ""},
                {"column_name": "visit_time", "data_type": "TIMESTAMP", "column_comment": ""},
            ],
        }
    ]
}
EVIDENCE = [{"table_name": "visit_record", "evidence_score": 1.0, "strong_evidence": True}]
CONFIG = {
    "api_key": "secret",
    "base_url": "https://example.test/v1",
    "model_name": "test-model",
    "enable_thinking": True,
    "thinking_timeout_seconds": 120,
}


def _record(attempt: int, *, prompt_tokens: int = 10, completion_tokens: int = 5, error_code=None):
    return ModelCallRecord(
        stage_name="SQL 生成" if attempt == 1 else "SQL 修复",
        attempt=attempt,
        model_name="test-model",
        status_code=200,
        provider_request_id=f"r{attempt}",
        duration_ms=2,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error_code=error_code,
    )


def _result(attempt: int, sql: str, reason: str = ""):
    return ModelCallResult(
        candidate=ModelCandidate(sql=sql, reason=reason, assumptions=()),
        record=_record(attempt),
    )


def _run(monkeypatch, responses, **overrides):
    calls = []

    async def fake_request(*args, **kwargs):
        calls.append(kwargs["attempt"])
        response = responses[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("backend.generation.request_model_candidate", fake_request)
    values = {
        "model_config": CONFIG,
        "target_db_type": "mysql",
        "natural_text": "查询 visit_record",
        "schema_bundle": SCHEMA,
        "schema_evidence": EVIDENCE,
    }
    values.update(overrides)
    return asyncio.run(orchestrate_sql_generation(**values)), calls


def test_valid_sql_uses_one_call_even_when_repair_enabled(monkeypatch) -> None:
    result, calls = _run(monkeypatch, [_result(1, "SELECT id FROM visit_record")])
    assert calls == [1]
    assert result.validation_status == "passed"
    assert result.model_calls == 1
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5


def test_entry_deadline_reduces_available_model_timeout(monkeypatch) -> None:
    observed_timeouts = []

    async def fake_request(*_args, **kwargs):
        observed_timeouts.append(kwargs["request_timeout_seconds"])
        return _result(1, "SELECT id FROM visit_record")

    monkeypatch.setattr("backend.generation.request_model_candidate", fake_request)
    monkeypatch.setattr("backend.generation.monotonic", lambda: 160.0)

    result = asyncio.run(
        orchestrate_sql_generation(
            model_config=CONFIG,
            target_db_type="mysql",
            natural_text="查询 visit_record",
            schema_bundle=SCHEMA,
            schema_evidence=EVIDENCE,
            request_started_at=100.0,
        )
    )

    assert result.validation_status == "passed"
    assert observed_timeouts == [60.0]


def test_expired_entry_deadline_never_calls_model(monkeypatch) -> None:
    called = False

    async def fake_request(*_args, **_kwargs):
        nonlocal called
        called = True
        return _result(1, "SELECT id FROM visit_record")

    monkeypatch.setattr("backend.generation.request_model_candidate", fake_request)
    monkeypatch.setattr("backend.generation.monotonic", lambda: 221.0)

    with pytest.raises(GenerationError) as error:
        asyncio.run(
            orchestrate_sql_generation(
                model_config=CONFIG,
                target_db_type="mysql",
                natural_text="查询 visit_record",
                schema_bundle=SCHEMA,
                schema_evidence=EVIDENCE,
                request_started_at=100.0,
            )
        )

    assert error.value.status_code == 504
    assert error.value.error_code == "MODEL_TIMEOUT"
    assert error.value.model_calls == 0
    assert called is False


def test_invalid_first_candidate_gets_one_repair(monkeypatch) -> None:
    result, calls = _run(
        monkeypatch,
        [_result(1, "DELETE FROM visit_record"), _result(2, "SELECT id FROM visit_record")],
    )
    assert calls == [1, 2]
    assert result.validation_status == "passed"
    assert result.model_calls == 2
    assert result.prompt_tokens == 20


def test_legacy_enable_thinking_flag_disables_repair(monkeypatch) -> None:
    result, calls = _run(
        monkeypatch,
        [_result(1, "DELETE FROM visit_record"), _result(2, "SELECT id FROM visit_record")],
        model_config={**CONFIG, "enable_thinking": False},
    )

    assert calls == [1]
    assert result.sql == "NO_SQL"
    assert result.validation_status == "failed"
    assert result.no_sql_code == "VALIDATION_FAILED"
    assert result.model_calls == 1


def test_second_invalid_candidate_returns_failed_no_sql(monkeypatch) -> None:
    result, calls = _run(
        monkeypatch,
        [_result(1, "SELECT missing FROM visit_record"), _result(2, "SELECT still_missing FROM visit_record")],
    )
    assert calls == [1, 2]
    assert result.sql == "NO_SQL"
    assert result.validation_status == "failed"
    assert result.no_sql_code == "VALIDATION_FAILED"
    assert result.model_calls == 2


def test_low_evidence_never_calls_model(monkeypatch) -> None:
    result, calls = _run(monkeypatch, [], schema_evidence=[])
    assert calls == []
    assert result.sql == "NO_SQL"
    assert result.model_calls == 0
    assert result.no_sql_code == "LOW_SCHEMA_EVIDENCE"


def test_first_contract_error_can_be_repaired_once(monkeypatch) -> None:
    contract_error = ModelContractError(
        status_code=502,
        error_code="MODEL_RESPONSE_INVALID",
        message="bad json",
        record=_record(1, error_code="MODEL_RESPONSE_INVALID"),
        candidate_output="not json",
    )
    result, calls = _run(monkeypatch, [contract_error, _result(2, "SELECT id FROM visit_record")])
    assert calls == [1, 2]
    assert result.validation_status == "passed"
    assert result.model_calls == 2


def test_second_contract_error_raises_stable_generation_error(monkeypatch) -> None:
    first = ModelContractError(
        status_code=502,
        error_code="MODEL_RESPONSE_INVALID",
        message="bad one",
        record=_record(1, error_code="MODEL_RESPONSE_INVALID"),
        candidate_output="bad one",
    )
    second = ModelContractError(
        status_code=502,
        error_code="MODEL_RESPONSE_INVALID",
        message="bad two",
        record=_record(2, error_code="MODEL_RESPONSE_INVALID"),
        candidate_output="bad two",
    )
    with pytest.raises(GenerationError) as error:
        _run(monkeypatch, [first, second])
    assert error.value.status_code == 502
    assert error.value.error_code == "MODEL_RESPONSE_INVALID"
    assert error.value.model_calls == 2


def test_model_declined_is_not_locally_validated(monkeypatch) -> None:
    result, calls = _run(monkeypatch, [_result(1, "NO_SQL", "Schema 缺少所需字段")])
    assert calls == [1]
    assert result.validation_status == "not_run"
    assert result.no_sql_code == "MODEL_DECLINED"


def test_explicit_write_request_is_rejected_before_model(monkeypatch) -> None:
    with pytest.raises(GenerationError) as error:
        _run(monkeypatch, [], natural_text="请删除就诊记录")
    assert error.value.error_code == "UNSUPPORTED_OPERATION"
    assert error.value.model_calls == 0
