from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.config import settings
from backend.crud import create_db_definition, create_user, replace_table_schema, update_model_config
from backend.database import db_session, init_db
from backend.generation import GenerationError, GenerationResult
from backend.main import app
from backend.schemas import (
    ColumnUpload,
    ConfigUpdate,
    DbDefinitionCreate,
    TableUpload,
    TableUploadRequest,
    UserCreateRequest,
)


def _result(sql: str = "SELECT id FROM orders") -> GenerationResult:
    return GenerationResult(
        sql=sql,
        reason="" if sql != "NO_SQL" else "无法生成",
        assumptions=(),
        validation_status="passed" if sql != "NO_SQL" else "failed",
        validation_errors=(),
        warnings=(),
        tables=("orders",) if sql != "NO_SQL" else (),
        columns=("orders.id",) if sql != "NO_SQL" else (),
        prompt_version="his-sql-v1",
        policy_version="sql-policy-v1",
        context_hash="b" * 64,
        model_calls=1,
        prompt_tokens=20,
        completion_tokens=8,
        duration_ms=15,
        prompt_chars=400,
        no_sql_code="" if sql != "NO_SQL" else "VALIDATION_FAILED",
        call_records=(),
    )


class GenerateSqlApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(settings, "db_path", Path(self.temp.name) / "test.db"),
            patch.object(settings, "app_host", "127.0.0.1"),
        ]
        for item in self.patches:
            item.start()
        init_db()
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                self.user = create_user(connection, UserCreateRequest(username="generate-user", password="safe-password"))
                self.database = create_db_definition(
                    connection,
                    DbDefinitionCreate(name="generate-db", db_type="mysql"),
                    self.user["id"],
                )
                replace_table_schema(
                    connection,
                    self.database["id"],
                    TableUploadRequest(
                        tables=[
                            TableUpload(
                                table_name="orders",
                                table_comment="订单",
                                columns=[ColumnUpload(column_name="id", data_type="INT", column_comment="订单编号")],
                            )
                        ]
                    ),
                )
        self.token = create_access_token(
            {
                "sub": str(self.user["id"]),
                "username": self.user["username"],
                "role": self.user["role"],
                "token_version": str(self.user["token_version"]),
            }
        )
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_valid_generation_returns_trace_contract(self) -> None:
        orchestrator = AsyncMock(return_value=_result())
        with patch("backend.main.orchestrate_sql_generation", orchestrator), patch(
            "backend.rag._vector_search_available", return_value=False
        ), patch("backend.main.monotonic", return_value=123.0):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={"db_id": self.database["id"], "natural_text": "list orders", "target_db_type": "mysql"},
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["validation_status"], "passed")
        self.assertEqual(data["model_calls"], 1)
        self.assertTrue(data["request_id"])
        self.assertEqual(data["retrieved_evidence"][0]["table_name"], "orders")
        self.assertEqual(orchestrator.await_args.kwargs["request_started_at"], 123.0)
        with db_session() as connection:
            trace = connection.execute(
                "SELECT * FROM generation_trace WHERE request_id = ?", (data["request_id"],)
            ).fetchone()
        self.assertEqual(trace["outcome"], "passed")
        self.assertEqual(trace["history_id"], data["history_id"])

    def test_generation_uses_persisted_schema_rag_top_k(self) -> None:
        with db_session() as connection:
            update_model_config(
                connection,
                ConfigUpdate(
                    base_url="https://example.test/v1",
                    model_name="test-model",
                    rag_top_k=12,
                ),
            )

        from backend.rag import retrieve_schema_context as real_retrieve_schema_context

        with patch("backend.main.orchestrate_sql_generation", AsyncMock(return_value=_result())), patch(
            "backend.main.retrieve_schema_context", wraps=real_retrieve_schema_context
        ) as retrieve_schema_context, patch("backend.rag._vector_search_available", return_value=False):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={"db_id": self.database["id"], "natural_text": "list orders", "target_db_type": "mysql"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(retrieve_schema_context.call_args.kwargs["top_k"], 12)

    def test_low_evidence_returns_no_sql_without_remote_call(self) -> None:
        orchestrator = AsyncMock()
        from backend.generation import orchestrate_sql_generation as real_orchestrator

        orchestrator.side_effect = real_orchestrator
        with patch("backend.main.orchestrate_sql_generation", orchestrator), patch(
            "backend.rag._vector_search_available", return_value=False
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={"db_id": self.database["id"], "natural_text": "今天天气如何", "target_db_type": "mysql"},
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["sql"], "NO_SQL")
        self.assertEqual(data["model_calls"], 0)
        self.assertEqual(data["no_sql_code"], "LOW_SCHEMA_EVIDENCE")
        self.assertGreater(data["history_id"], 0)

    def test_empty_schema_returns_no_sql_with_history_and_trace(self) -> None:
        with db_session() as connection:
            replace_table_schema(
                connection,
                self.database["id"],
                TableUploadRequest(tables=[]),
            )

        from backend.generation import orchestrate_sql_generation as real_orchestrator

        orchestrator = AsyncMock(side_effect=real_orchestrator)
        with patch("backend.main.orchestrate_sql_generation", new=orchestrator), patch(
            "backend.rag._vector_search_available", return_value=False
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={"db_id": self.database["id"], "natural_text": "列出订单", "target_db_type": "mysql"},
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["sql"], "NO_SQL")
        self.assertEqual(data["no_sql_code"], "LOW_SCHEMA_EVIDENCE")
        self.assertEqual(data["model_calls"], 0)
        self.assertGreater(data["history_id"], 0)
        orchestrator.assert_awaited_once()
        with db_session() as connection:
            trace = connection.execute(
                "SELECT history_id, outcome, policy_status, model_calls FROM generation_trace WHERE request_id = ?",
                (data["request_id"],),
            ).fetchone()
        self.assertEqual(trace["history_id"], data["history_id"])
        self.assertEqual(trace["outcome"], "no_sql")
        self.assertEqual(trace["policy_status"], "not_run")
        self.assertEqual(trace["model_calls"], 0)

    def test_dialect_mismatch_never_calls_orchestrator(self) -> None:
        orchestrator = AsyncMock(return_value=_result())
        with patch("backend.main.orchestrate_sql_generation", orchestrator), patch(
            "backend.rag._vector_search_available", return_value=False
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={"db_id": self.database["id"], "natural_text": "list orders", "target_db_type": "pg"},
                )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "DIALECT_MISMATCH")
        request_id = response.json()["detail"]["request_id"]
        with db_session() as connection:
            trace = connection.execute(
                "SELECT outcome, error_code, model_calls FROM generation_trace WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        self.assertEqual(trace["outcome"], "error")
        self.assertEqual(trace["error_code"], "DIALECT_MISMATCH")
        self.assertEqual(trace["model_calls"], 0)
        orchestrator.assert_not_awaited()

    def test_remote_error_writes_error_trace(self) -> None:
        orchestrator = AsyncMock(
            side_effect=GenerationError(
                status_code=504,
                error_code="MODEL_TIMEOUT",
                message="timeout",
                model_calls=1,
                duration_ms=30,
            )
        )
        with patch("backend.main.orchestrate_sql_generation", orchestrator), patch(
            "backend.rag._vector_search_available", return_value=False
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={"db_id": self.database["id"], "natural_text": "list orders", "target_db_type": "mysql"},
                )
        self.assertEqual(response.status_code, 504)
        request_id = response.json()["detail"]["request_id"]
        with db_session() as connection:
            trace = connection.execute(
                "SELECT history_id, outcome, error_code, model_calls FROM generation_trace WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        self.assertIsNone(trace["history_id"])
        self.assertEqual(trace["outcome"], "error")
        self.assertEqual(trace["error_code"], "MODEL_TIMEOUT")
        self.assertEqual(trace["model_calls"], 1)

    def test_remote_phase_has_no_open_db_session(self) -> None:
        import backend.main as main_module

        active_sessions = 0
        original = db_session

        @contextmanager
        def tracked_session():
            nonlocal active_sessions
            active_sessions += 1
            try:
                with original() as connection:
                    yield connection
            finally:
                active_sessions -= 1

        async def assert_closed(*_args, **_kwargs):
            self.assertEqual(active_sessions, 0)
            return _result()

        with patch.object(main_module, "db_session", tracked_session), patch.object(
            main_module, "orchestrate_sql_generation", side_effect=assert_closed
        ), patch("backend.rag._vector_search_available", return_value=False):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={"db_id": self.database["id"], "natural_text": "list orders", "target_db_type": "mysql"},
                )
        self.assertEqual(response.status_code, 200)

    def test_unknown_explicit_identifier_never_reaches_remote_model(self) -> None:
        with patch("backend.generation.request_model_candidate", new=AsyncMock()) as remote_call, patch(
            "backend.rag._vector_search_available", return_value=False
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/generate-sql",
                    headers=self.headers,
                    json={
                        "db_id": self.database["id"],
                        "natural_text": "查询 orders 和 missing_table.id",
                        "target_db_type": "mysql",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sql"], "NO_SQL")
        self.assertEqual(response.json()["no_sql_code"], "LOW_SCHEMA_EVIDENCE")
        remote_call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
