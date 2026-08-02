from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from time import monotonic
from typing import Any, Callable, Literal, Mapping, Sequence, TypedDict

import httpx
from fastapi import HTTPException

from .config import normalize_reasoning_effort
from .prompting import compile_generation_prompt, evidence_from_legacy_ddl


logger = logging.getLogger(__name__)

SQL_GENERATION_TEMPERATURE = 0.1
MODEL_OUTPUT_MAX_CHARS = 30_000
MODEL_SQL_MAX_CHARS = 20_000
MODEL_REASON_MAX_CHARS = 2_000
MODEL_ASSUMPTIONS_MAX_ITEMS = 20
MODEL_ASSUMPTION_MAX_CHARS = 500


class SqlGenerationResult(TypedDict):
    sql: str
    reason: str
    assumptions: list[str]


@dataclass(frozen=True)
class ModelCandidate:
    sql: str
    reason: str
    assumptions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"sql": self.sql, "reason": self.reason, "assumptions": list(self.assumptions)}


@dataclass(frozen=True)
class ModelCallEvent:
    phase: Literal["started", "completed"]
    stage_name: str
    attempt: int
    model_name: str
    status_code: int | None = None
    provider_request_id: str | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ModelCallRecord:
    stage_name: str
    attempt: int
    model_name: str
    status_code: int | None
    provider_request_id: str | None
    duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelCallResult:
    candidate: ModelCandidate
    record: ModelCallRecord


ModelCallObserver = Callable[[ModelCallEvent], None]


class ModelGatewayError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        record: ModelCallRecord | None = None,
        candidate_output: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.record = record
        # Transient repair input only. It must never enter persistent trace data.
        self.candidate_output = candidate_output


class ModelContractError(ModelGatewayError):
    pass


class _DuplicateJsonKeyError(ValueError):
    pass


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def normalize_chat_completion_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


_normalize_chat_completion_url = normalize_chat_completion_url


def validate_model_config(model_config: Mapping[str, Any]) -> None:
    missing = [key for key in ("api_key", "base_url", "model_name") if not str(model_config.get(key, "")).strip()]
    if missing:
        raise ModelGatewayError(
            status_code=400,
            error_code="MODEL_CONFIG_INVALID",
            message="大模型配置不完整，请填写 API Key、Base URL 和模型名。",
        )


def parse_model_candidate(content: str) -> ModelCandidate:
    if not isinstance(content, str):
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应 content 必须是字符串。",
        )
    if len(content) > MODEL_OUTPUT_MAX_CHARS:
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应超过字符上限。",
            candidate_output=content[:MODEL_OUTPUT_MAX_CHARS],
        )
    try:
        payload = json.loads(content, object_pairs_hook=_strict_object_pairs)
    except (json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应不是严格 JSON 对象。",
            candidate_output=content,
        ) from exc
    if not isinstance(payload, dict):
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应根值必须是 JSON 对象。",
            candidate_output=content,
        )
    expected_keys = {"sql", "reason", "assumptions"}
    if set(payload) != expected_keys:
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应字段必须且只能包含 sql、reason、assumptions。",
            candidate_output=content,
        )
    sql = payload["sql"]
    reason = payload["reason"]
    assumptions = payload["assumptions"]
    if not isinstance(sql, str) or not isinstance(reason, str):
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应 sql 和 reason 必须是字符串。",
            candidate_output=content,
        )
    if not isinstance(assumptions, list) or any(not isinstance(item, str) for item in assumptions):
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应 assumptions 必须是字符串数组。",
            candidate_output=content,
        )
    sql = sql.strip()
    reason = reason.strip()
    normalized_assumptions = tuple(item.strip() for item in assumptions)
    if not sql or len(sql) > MODEL_SQL_MAX_CHARS:
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应 sql 为空或超过字符上限。",
            candidate_output=content,
        )
    if len(reason) > MODEL_REASON_MAX_CHARS:
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应 reason 超过字符上限。",
            candidate_output=content,
        )
    if len(normalized_assumptions) > MODEL_ASSUMPTIONS_MAX_ITEMS or any(
        len(item) > MODEL_ASSUMPTION_MAX_CHARS for item in normalized_assumptions
    ):
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="模型响应 assumptions 超过数量或字符上限。",
            candidate_output=content,
        )
    if sql == "NO_SQL":
        if not reason:
            raise ModelContractError(
                status_code=502,
                error_code="MODEL_RESPONSE_INVALID",
                message="NO_SQL 响应必须提供具体 reason。",
                candidate_output=content,
            )
    elif reason:
        raise ModelContractError(
            status_code=502,
            error_code="MODEL_RESPONSE_INVALID",
            message="成功 SQL 响应的 reason 必须为空。",
            candidate_output=content,
        )
    return ModelCandidate(sql=sql, reason=reason, assumptions=normalized_assumptions)


def _build_rag_prompt(
    *,
    target_db_type: str,
    operation: str,
    question: str,
    retrieved_tables_ddl: str,
    feedback_examples: list[dict[str, str]] | None = None,
) -> str:
    """Legacy test/caller adapter returning new compiler's JSON user message."""

    package = compile_generation_prompt(
        dialect=target_db_type,
        intent={"operation": operation},
        his_semantics=(),
        schema_evidence=evidence_from_legacy_ddl(retrieved_tables_ddl),
        verified_examples=feedback_examples or (),
        user_request=question,
    )
    return package.user_message


def _usage_value(usage: Mapping[str, Any], key: str) -> int | None:
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _provider_request_id(response: httpx.Response) -> str | None:
    for key in ("x-request-id", "request-id", "openai-request-id"):
        value = response.headers.get(key)
        if value:
            return value[:200]
    return None


def _reasoning_effort_unsupported(response: httpx.Response) -> bool:
    """Recognize a provider rejecting the optional reasoning extension."""

    if response.status_code != 400:
        return False
    try:
        body = response.json()
        text = json.dumps(body, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        text = response.text
    lowered = text.casefold()
    return "reasoning_effort" in lowered and any(
        marker in lowered
        for marker in ("unsupported", "unknown", "unrecognized", "invalid", "not allowed", "additional")
    )


def _notify(observer: ModelCallObserver | None, event: ModelCallEvent) -> None:
    if observer is None:
        return
    try:
        observer(event)
    except Exception:
        logger.exception("Model call observer failed")


def _record(
    *,
    stage_name: str,
    attempt: int,
    model_name: str,
    started_at: float,
    status_code: int | None,
    provider_request_id: str | None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error_code: str | None = None,
) -> ModelCallRecord:
    return ModelCallRecord(
        stage_name=stage_name,
        attempt=attempt,
        model_name=model_name,
        status_code=status_code,
        provider_request_id=provider_request_id,
        duration_ms=max(int((monotonic() - started_at) * 1000), 0),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error_code=error_code,
    )


def _completed_event(record: ModelCallRecord) -> ModelCallEvent:
    return ModelCallEvent(
        phase="completed",
        stage_name=record.stage_name,
        attempt=record.attempt,
        model_name=record.model_name,
        status_code=record.status_code,
        provider_request_id=record.provider_request_id,
        duration_ms=record.duration_ms,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        error_code=record.error_code,
    )


def _stream_upstream_message(error: Any) -> str:
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:500]
    if isinstance(error, str) and error.strip():
        return error.strip()[:500]
    return "大模型流式响应返回了错误事件。"


def _stream_contract_error(message: str, *, candidate_output: str = "") -> ModelContractError:
    return ModelContractError(
        status_code=502,
        error_code="MODEL_RESPONSE_INVALID",
        message=message,
        candidate_output=candidate_output,
    )


@dataclass
class _StreamUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


async def _collect_streamed_content(response: httpx.Response, usage_state: _StreamUsage) -> str:
    """Aggregate an OpenAI-compatible SSE response without exposing partial SQL."""

    parts: list[str] = []
    event_data: list[str] = []
    done = False

    def process_event(data_lines: list[str]) -> bool:
        if not data_lines:
            return False
        data = "\n".join(data_lines).strip()
        if not data:
            return False
        if data == "[DONE]":
            return True
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise _stream_contract_error("大模型流式响应包含无效 JSON 事件。", candidate_output="".join(parts)) from exc
        if not isinstance(payload, Mapping):
            raise _stream_contract_error("大模型流式响应事件必须是 JSON 对象。", candidate_output="".join(parts))
        if "error" in payload:
            raise ModelGatewayError(
                status_code=502,
                error_code="MODEL_UPSTREAM_ERROR",
                message=_stream_upstream_message(payload["error"]),
            )

        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            next_prompt_tokens = _usage_value(usage, "prompt_tokens")
            next_completion_tokens = _usage_value(usage, "completion_tokens")
            if next_prompt_tokens is not None:
                usage_state.prompt_tokens = next_prompt_tokens
            if next_completion_tokens is not None:
                usage_state.completion_tokens = next_completion_tokens

        choices = payload.get("choices")
        if choices is None:
            return False
        if not isinstance(choices, list):
            raise _stream_contract_error("大模型流式响应 choices 必须是数组。", candidate_output="".join(parts))
        if not choices:
            return False
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise _stream_contract_error("大模型流式响应 choice 必须是对象。", candidate_output="".join(parts))
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            if not isinstance(finish_reason, str) or finish_reason != "stop":
                raise _stream_contract_error(
                    "大模型未正常完成流式输出。",
                    candidate_output="".join(parts),
                )
        delta = choice.get("delta")
        if delta is None:
            return False
        if not isinstance(delta, Mapping):
            raise _stream_contract_error("大模型流式响应 delta 必须是对象。", candidate_output="".join(parts))
        content = delta.get("content")
        if content is None:
            return False
        if not isinstance(content, str):
            raise _stream_contract_error("大模型流式响应 content 必须是字符串。", candidate_output="".join(parts))
        if content:
            parts.append(content)
            aggregate = "".join(parts)
            if len(aggregate) > MODEL_OUTPUT_MAX_CHARS:
                raise _stream_contract_error("大模型流式响应超过字符上限。", candidate_output=aggregate[:MODEL_OUTPUT_MAX_CHARS])
        return False

    async for line in response.aiter_lines():
        if line == "":
            done = process_event(event_data)
            event_data = []
            if done:
                break
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data = line[5:]
            if data.startswith(" "):
                data = data[1:]
            event_data.append(data)

    if not done and event_data:
        done = process_event(event_data)
    if not done:
        raise _stream_contract_error("大模型流式响应在收到 [DONE] 前中断。", candidate_output="".join(parts))
    return "".join(parts)


async def request_model_candidate(
    model_config: Mapping[str, Any],
    *,
    messages: Sequence[Mapping[str, str]],
    stage_name: str,
    attempt: int,
    request_timeout_seconds: float,
    call_observer: ModelCallObserver | None = None,
) -> ModelCallResult:
    """Call OpenAI-compatible chat completions and strictly parse one candidate."""

    validate_model_config(model_config)
    if attempt not in (1, 2):
        raise ValueError("attempt 只能是 1 或 2。")
    model_name = str(model_config["model_name"]).strip()
    started_at = monotonic()
    _notify(
        call_observer,
        ModelCallEvent(
            phase="started",
            stage_name=stage_name,
            attempt=attempt,
            model_name=model_name,
        ),
    )
    reasoning_effort = normalize_reasoning_effort(model_config.get("reasoning_effort"))
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [dict(message) for message in messages],
        "stream": True,
    }
    if reasoning_effort is not None:
        # Reasoning models commonly reject temperature; select a compatible
        # request shape when the administrator explicitly opts in.
        payload["reasoning_effort"] = reasoning_effort
    else:
        payload["temperature"] = SQL_GENERATION_TEMPERATURE
    response: httpx.Response | None = None
    provider_id: str | None = None
    usage_state = _StreamUsage()
    candidate: ModelCandidate | None = None
    timeout_seconds = max(float(request_timeout_seconds), 0.001)
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    normalize_chat_completion_url(str(model_config["base_url"])),
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {model_config['api_key']}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    provider_id = _provider_request_id(response)
                    if response.status_code < 200 or response.status_code >= 300:
                        # Read the stream before classifying provider errors. httpx otherwise
                        # raises ResponseNotRead when the compatibility check reads its body.
                        await response.aread()
                        response.raise_for_status()
                    content = await _collect_streamed_content(response, usage_state)
                    candidate = parse_model_candidate(content)
    except (TimeoutError, httpx.TimeoutException) as exc:
        record = _record(
            stage_name=stage_name,
            attempt=attempt,
            model_name=model_name,
            started_at=started_at,
            status_code=response.status_code if response is not None else None,
            provider_request_id=provider_id,
            prompt_tokens=usage_state.prompt_tokens,
            completion_tokens=usage_state.completion_tokens,
            error_code="MODEL_TIMEOUT",
        )
        _notify(call_observer, _completed_event(record))
        raise ModelGatewayError(
            status_code=504,
            error_code="MODEL_TIMEOUT",
            message=f"大模型在{stage_name}阶段超时。",
            record=record,
        ) from exc
    except httpx.HTTPStatusError as exc:
        response = exc.response
        provider_id = _provider_request_id(response)
        unsupported_reasoning = reasoning_effort is not None and _reasoning_effort_unsupported(response)
        error_code = "MODEL_REASONING_EFFORT_UNSUPPORTED" if unsupported_reasoning else "MODEL_UPSTREAM_ERROR"
        record = _record(
            stage_name=stage_name,
            attempt=attempt,
            model_name=model_name,
            started_at=started_at,
            status_code=response.status_code,
            provider_request_id=provider_id,
            error_code=error_code,
        )
        _notify(call_observer, _completed_event(record))
        if unsupported_reasoning:
            raise ModelGatewayError(
                status_code=502,
                error_code=error_code,
                message="The configured reasoning_effort is not supported by the model provider.",
                record=record,
            ) from exc
        raise ModelGatewayError(
            status_code=502,
            error_code="MODEL_UPSTREAM_ERROR",
            message="大模型服务返回错误状态。",
            record=record,
        ) from exc
    except ModelContractError as exc:
        record = _record(
            stage_name=stage_name,
            attempt=attempt,
            model_name=model_name,
            started_at=started_at,
            status_code=response.status_code if response is not None else None,
            provider_request_id=provider_id,
            prompt_tokens=usage_state.prompt_tokens,
            completion_tokens=usage_state.completion_tokens,
            error_code=exc.error_code,
        )
        _notify(call_observer, _completed_event(record))
        raise ModelContractError(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            record=record,
            candidate_output=exc.candidate_output,
        ) from exc
    except ModelGatewayError as exc:
        record = _record(
            stage_name=stage_name,
            attempt=attempt,
            model_name=model_name,
            started_at=started_at,
            status_code=response.status_code if response is not None else None,
            provider_request_id=provider_id,
            prompt_tokens=usage_state.prompt_tokens,
            completion_tokens=usage_state.completion_tokens,
            error_code=exc.error_code,
        )
        _notify(call_observer, _completed_event(record))
        raise ModelGatewayError(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            record=record,
            candidate_output=exc.candidate_output,
        ) from exc
    except httpx.HTTPError as exc:
        record = _record(
            stage_name=stage_name,
            attempt=attempt,
            model_name=model_name,
            started_at=started_at,
            status_code=response.status_code if response is not None else None,
            provider_request_id=provider_id,
            prompt_tokens=usage_state.prompt_tokens,
            completion_tokens=usage_state.completion_tokens,
            error_code="MODEL_UPSTREAM_ERROR",
        )
        _notify(call_observer, _completed_event(record))
        raise ModelGatewayError(
            status_code=502,
            error_code="MODEL_UPSTREAM_ERROR",
            message="无法连接大模型服务。",
            record=record,
        ) from exc
    assert response is not None
    assert candidate is not None
    record = _record(
        stage_name=stage_name,
        attempt=attempt,
        model_name=model_name,
        started_at=started_at,
        status_code=response.status_code,
        provider_request_id=provider_id,
        prompt_tokens=usage_state.prompt_tokens,
        completion_tokens=usage_state.completion_tokens,
    )
    _notify(call_observer, _completed_event(record))
    return ModelCallResult(candidate=candidate, record=record)


async def generate_sql_with_llm(
    model_config: Mapping[str, Any],
    *,
    target_db_type: str,
    natural_text: str,
    retrieved_tables_ddl: str,
    operation: str = "SELECT",
    feedback_examples: list[dict[str, str]] | None = None,
) -> SqlGenerationResult:
    """Compatibility adapter for the pre-v1 main route.

    New integration should call ``generation.orchestrate_sql_generation`` so the
    candidate receives complete local AST validation. This adapter intentionally
    performs only one model call; it never restores the old fixed two-call flow.
    """

    try:
        validate_model_config(model_config)
        if not retrieved_tables_ddl.strip():
            raise ModelGatewayError(
                status_code=400,
                error_code="MODEL_CONFIG_INVALID",
                message="没有可用的表结构上下文，无法生成 SQL。",
            )
        prompt = compile_generation_prompt(
            dialect=target_db_type,
            intent={"operation": operation},
            his_semantics=(),
            schema_evidence=evidence_from_legacy_ddl(retrieved_tables_ddl),
            verified_examples=feedback_examples or (),
            user_request=natural_text,
        )
        timeout = min(max(int(model_config.get("thinking_timeout_seconds", 600)), 10), 600)
        result = await request_model_candidate(
            model_config,
            messages=prompt.messages,
            stage_name="SQL 生成",
            attempt=1,
            request_timeout_seconds=float(timeout),
        )
        return {
            "sql": result.candidate.sql,
            "reason": result.candidate.reason,
            "assumptions": list(result.candidate.assumptions),
        }
    except ModelGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


__all__ = [
    "MODEL_OUTPUT_MAX_CHARS",
    "ModelCallEvent",
    "ModelCallObserver",
    "ModelCallRecord",
    "ModelCallResult",
    "ModelCandidate",
    "ModelContractError",
    "ModelGatewayError",
    "generate_sql_with_llm",
    "normalize_chat_completion_url",
    "parse_model_candidate",
    "request_model_candidate",
    "validate_model_config",
]
