from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.generation import GenerationError, orchestrate_sql_generation
from backend.llm import (
    ModelCallRecord,
    ModelCallResult,
    ModelCandidate,
    ModelContractError,
    ModelGatewayError,
    parse_model_candidate,
    request_model_candidate,
)
from backend.schemas import DbDefinitionCreate, GenerateSqlRequest


ROOT = Path(__file__).resolve().parents[1]

SCHEMA = {
    "tables": [
        {
            "table_name": "patients",
            "columns": [
                {"column_name": "id", "data_type": "INT"},
                {"column_name": "name", "data_type": "VARCHAR"},
            ],
        }
    ]
}
EVIDENCE = [{"table_name": "patients", "evidence_score": 1.0, "strong_evidence": True}]
CONFIG = {
    "api_key": "not-sent-by-test",
    "base_url": "https://must-not-be-contacted.invalid/v1",
    "model_name": "fake-model",
    "enable_thinking": True,
    "thinking_timeout_seconds": 600,
}


def _call_result(attempt: int, sql: str, reason: str = "") -> ModelCallResult:
    return ModelCallResult(
        candidate=ModelCandidate(sql=sql, reason=reason, assumptions=()),
        record=ModelCallRecord(
            stage_name="SQL 生成" if attempt == 1 else "SQL 修复",
            attempt=attempt,
            model_name="fake-model",
            status_code=200,
            provider_request_id=f"fake-{attempt}",
            duration_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
        ),
    )


class GenerationContractAdversarialTests(unittest.TestCase):
    def test_model_request_has_strict_wall_clock_timeout(self) -> None:
        events = []

        class HangingStream:
            headers = {"x-request-id": "stream-timeout-id"}
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def aiter_lines(self):
                await asyncio.Event().wait()
                yield ""

        class HangingClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def stream(self, *_args, **_kwargs):
                return HangingStream()

        async def invoke() -> None:
            with patch("backend.llm.httpx.AsyncClient", HangingClient):
                with self.assertRaises(ModelGatewayError) as caught:
                    await asyncio.wait_for(
                        request_model_candidate(
                            CONFIG,
                            messages=[{"role": "user", "content": "test"}],
                            stage_name="SQL 生成",
                            attempt=1,
                            request_timeout_seconds=0.01,
                            call_observer=events.append,
                        ),
                        timeout=1.0,
                    )
            self.assertEqual(caught.exception.status_code, 504)
            self.assertEqual(caught.exception.error_code, "MODEL_TIMEOUT")
            assert caught.exception.record is not None
            self.assertEqual(caught.exception.record.status_code, 200)
            self.assertEqual(caught.exception.record.provider_request_id, "stream-timeout-id")

        asyncio.run(invoke())
        self.assertEqual([event.phase for event in events], ["started", "completed"])
        self.assertEqual(events[-1].error_code, "MODEL_TIMEOUT")

    def test_model_output_rejects_wrappers_extra_fields_and_type_coercion(self) -> None:
        invalid_values = (
            "SELECT id FROM patients",
            '```json\n{"sql":"SELECT id FROM patients","reason":"","assumptions":[]}\n```',
            '{"sql":"SELECT id FROM patients","reason":"","assumptions":[]} trailing',
            '{"sql":"SELECT id FROM patients","reason":""}',
            '{"sql":"SELECT id FROM patients","reason":"","assumptions":[],"validated":true}',
            '{"sql":1,"reason":"","assumptions":[]}',
            '{"sql":"SELECT id FROM patients","reason":"","assumptions":"none"}',
            '{"sql":"SELECT id FROM patients","reason":"model says safe","assumptions":[]}',
            '{"sql":"NO_SQL","reason":"","assumptions":[]}',
        )
        for content in invalid_values:
            with self.subTest(content=content):
                with self.assertRaises(ModelContractError):
                    parse_model_candidate(content)

    def test_duplicate_json_member_names_are_rejected_as_ambiguous(self) -> None:
        content = (
            '{"sql":"DROP TABLE patients","sql":"SELECT id FROM patients",'
            '"reason":"","assumptions":[]}'
        )
        with self.assertRaises(ModelContractError):
            parse_model_candidate(content)

    def test_valid_candidate_preserves_contract_types(self) -> None:
        candidate = parse_model_candidate(
            '{"sql":"SELECT id FROM patients","reason":"","assumptions":["uses imported Schema"]}'
        )
        self.assertEqual(candidate.sql, "SELECT id FROM patients")
        self.assertEqual(candidate.reason, "")
        self.assertEqual(candidate.assumptions, ("uses imported Schema",))

    def test_invalid_sql_gets_exactly_one_repair_and_never_third_call(self) -> None:
        calls: list[int] = []

        async def fake_request(*_args, **kwargs):
            attempt = kwargs["attempt"]
            calls.append(attempt)
            if len(calls) == 1:
                return _call_result(1, "DELETE FROM patients")
            if len(calls) == 2:
                return _call_result(2, "DROP TABLE patients")
            self.fail("generation attempted a forbidden third model call")

        with patch("backend.generation.request_model_candidate", new=fake_request):
            result = asyncio.run(
                orchestrate_sql_generation(
                    CONFIG,
                    target_db_type="mysql",
                    natural_text="查询 patients.id",
                    schema_bundle=SCHEMA,
                    schema_evidence=EVIDENCE,
                )
            )
        self.assertEqual(calls, [1, 2])
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.sql, "NO_SQL")
        self.assertEqual(result.validation_status, "failed")

    def test_two_invalid_json_outputs_stop_with_stable_error(self) -> None:
        calls: list[int] = []

        async def fake_request(*_args, **kwargs):
            attempt = kwargs["attempt"]
            calls.append(attempt)
            if attempt not in (1, 2):
                self.fail("generation attempted a forbidden third model call")
            raise ModelContractError(
                status_code=502,
                error_code="MODEL_RESPONSE_INVALID",
                message=f"invalid strict JSON attempt {attempt}",
                record=ModelCallRecord(
                    stage_name="SQL 生成" if attempt == 1 else "SQL 修复",
                    attempt=attempt,
                    model_name="fake-model",
                    status_code=200,
                    provider_request_id=f"fake-{attempt}",
                    duration_ms=1,
                    prompt_tokens=None,
                    completion_tokens=None,
                    error_code="MODEL_RESPONSE_INVALID",
                ),
                candidate_output="not-json",
            )

        with patch("backend.generation.request_model_candidate", new=fake_request):
            with self.assertRaises(GenerationError) as caught:
                asyncio.run(
                    orchestrate_sql_generation(
                        CONFIG,
                        target_db_type="mysql",
                        natural_text="查询 patients.id",
                        schema_bundle=SCHEMA,
                        schema_evidence=EVIDENCE,
                    )
                )
        self.assertEqual(calls, [1, 2])
        self.assertEqual(caught.exception.error_code, "MODEL_RESPONSE_INVALID")
        self.assertEqual(caught.exception.model_calls, 2)

    def test_network_failure_is_not_repaired(self) -> None:
        calls: list[int] = []

        async def fake_request(*_args, **kwargs):
            attempt = kwargs["attempt"]
            calls.append(attempt)
            raise ModelGatewayError(
                status_code=504,
                error_code="MODEL_TIMEOUT",
                message="fake timeout",
                record=ModelCallRecord(
                    stage_name="SQL 生成",
                    attempt=attempt,
                    model_name="fake-model",
                    status_code=None,
                    provider_request_id=None,
                    duration_ms=1,
                    prompt_tokens=None,
                    completion_tokens=None,
                    error_code="MODEL_TIMEOUT",
                ),
            )

        with patch("backend.generation.request_model_candidate", new=fake_request):
            with self.assertRaises(GenerationError) as caught:
                asyncio.run(
                    orchestrate_sql_generation(
                        CONFIG,
                        target_db_type="mysql",
                        natural_text="查询 patients.id",
                        schema_bundle=SCHEMA,
                        schema_evidence=EVIDENCE,
                    )
                )
        self.assertEqual(calls, [1])
        self.assertEqual(caught.exception.error_code, "MODEL_TIMEOUT")
        self.assertEqual(caught.exception.model_calls, 1)

    def test_valid_sql_uses_one_call_even_when_repair_is_enabled(self) -> None:
        calls: list[int] = []

        async def fake_request(*_args, **kwargs):
            calls.append(kwargs["attempt"])
            return _call_result(1, "SELECT id FROM patients")

        with patch("backend.generation.request_model_candidate", new=fake_request):
            result = asyncio.run(
                orchestrate_sql_generation(
                    CONFIG,
                    target_db_type="mysql",
                    natural_text="查询 patients.id",
                    schema_bundle=SCHEMA,
                    schema_evidence=EVIDENCE,
                )
            )
        self.assertEqual(calls, [1])
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.validation_status, "passed")

    def test_zero_schema_evidence_never_reaches_model_or_network(self) -> None:
        async def forbidden_request(*_args, **_kwargs):
            self.fail("model gateway called without strong Schema evidence")

        with patch("backend.generation.request_model_candidate", new=forbidden_request):
            result = asyncio.run(
                orchestrate_sql_generation(
                    CONFIG,
                    target_db_type="mysql",
                    natural_text="查询不存在的业务目标",
                    schema_bundle=SCHEMA,
                    schema_evidence=(),
                )
            )
        self.assertEqual(result.sql, "NO_SQL")
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(result.no_sql_code, "LOW_SCHEMA_EVIDENCE")

    def test_request_contract_has_no_target_database_connection_fields(self) -> None:
        forbidden = {
            "host",
            "hostname",
            "port",
            "username",
            "user",
            "password",
            "dsn",
            "connection_string",
            "database_url",
            "execute",
        }
        self.assertTrue(forbidden.isdisjoint(GenerateSqlRequest.model_fields))
        self.assertTrue(forbidden.isdisjoint(DbDefinitionCreate.model_fields))

    def test_backend_imports_no_target_database_driver(self) -> None:
        forbidden_roots = {
            "pymysql",
            "mysql",
            "mysqldb",
            "psycopg",
            "psycopg2",
            "asyncpg",
            "oracledb",
            "cx_oracle",
            "pyodbc",
            "sqlalchemy",
        }
        found: set[str] = set()
        for path in (ROOT / "backend").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split(".", 1)[0].casefold() for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module.split(".", 1)[0].casefold())
        self.assertFalse(found & forbidden_roots, found & forbidden_roots)

    def test_fastapi_source_declares_no_connect_execute_or_explain_route(self) -> None:
        tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
        paths: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                function = decorator.func
                if not isinstance(function, ast.Attribute) or not isinstance(function.value, ast.Name):
                    continue
                if function.value.id != "app" or function.attr not in {"get", "post", "put", "delete", "patch"}:
                    continue
                if isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                    paths.append(decorator.args[0].value.casefold())
        forbidden_segments = (
            "test-connection",
            "connect-database",
            "execute-sql",
            "query-execute",
            "explain-sql",
        )
        self.assertFalse(
            [path for path in paths if any(segment in path for segment in forbidden_segments)],
            paths,
        )


if __name__ == "__main__":
    unittest.main()
