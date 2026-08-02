from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from backend.llm import ModelGatewayError, request_model_candidate


BASE_CONFIG = {
    "api_key": "test-key",
    "base_url": "https://example.test/v1",
    "model_name": "test-model",
}


class _RecordingStream:
    def __init__(self, url: str):
        self._url = url

    async def __aenter__(self) -> httpx.Response:
        if _RecordingClient.status_code >= 400:
            content = json.dumps(_RecordingClient.response_body).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        else:
            content = "\n".join(_RecordingClient.sse_lines).encode("utf-8")
            headers = {"Content-Type": "text/event-stream"}
        return httpx.Response(
            _RecordingClient.status_code,
            content=content,
            headers=headers,
            request=httpx.Request("POST", self._url),
        )

    async def __aexit__(self, *_args):
        return None


class _RecordingClient:
    captured_payload: dict | None = None
    response_body: dict = {}
    sse_lines: list[str] = []
    status_code = 200

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, method, url, **kwargs):
        assert method == "POST"
        self.__class__.captured_payload = kwargs["json"]
        return _RecordingStream(url)


def _sse_chunk(content: str, *, finish_reason: str | None = None) -> str:
    return "data: " + json.dumps(
        {"choices": [{"delta": {"content": content}, "finish_reason": finish_reason}]}
    )


def _sse_lines(*chunks: str, done: bool = True) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        lines.extend((chunk, ""))
    if done:
        lines.extend(("data: [DONE]", ""))
    return lines


class ReasoningEffortRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        _RecordingClient.captured_payload = None
        _RecordingClient.response_body = {}
        _RecordingClient.sse_lines = _sse_lines(
            _sse_chunk('{"sql":"SELECT 1","reason":"","assumptions":[]}', finish_reason="stop")
        )
        _RecordingClient.status_code = 200

    def _invoke(self, config: dict):
        async def run():
            with patch("backend.llm.httpx.AsyncClient", _RecordingClient):
                return await request_model_candidate(
                    config,
                    messages=[{"role": "user", "content": "test"}],
                    stage_name="generation",
                    attempt=1,
                    request_timeout_seconds=1,
                )

        return asyncio.run(run())

    def test_default_request_keeps_temperature_and_omits_reasoning_effort(self) -> None:
        self._invoke(BASE_CONFIG)
        assert _RecordingClient.captured_payload is not None
        self.assertTrue(_RecordingClient.captured_payload["stream"])
        self.assertEqual(_RecordingClient.captured_payload["temperature"], 0.1)
        self.assertNotIn("reasoning_effort", _RecordingClient.captured_payload)

    def test_all_explicit_effort_values_omit_temperature(self) -> None:
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                self._invoke({**BASE_CONFIG, "reasoning_effort": effort})
                assert _RecordingClient.captured_payload is not None
                self.assertTrue(_RecordingClient.captured_payload["stream"])
                self.assertEqual(_RecordingClient.captured_payload["reasoning_effort"], effort)
                self.assertNotIn("temperature", _RecordingClient.captured_payload)

    def test_sse_content_is_aggregated_before_strict_candidate_parsing(self) -> None:
        _RecordingClient.sse_lines = _sse_lines(
            _sse_chunk('{"sql":"SELECT '),
            _sse_chunk('1","reason":"","assumptions":[]}', finish_reason="stop"),
        )

        result = self._invoke(BASE_CONFIG)

        self.assertEqual(result.candidate.sql, "SELECT 1")
        self.assertEqual(result.candidate.reason, "")
        self.assertEqual(result.candidate.assumptions, ())

    def test_provider_rejection_has_actionable_error_code(self) -> None:
        _RecordingClient.status_code = 400
        _RecordingClient.response_body = {
            "error": {"message": "Unknown parameter: reasoning_effort"}
        }

        async def run() -> None:
            with patch("backend.llm.httpx.AsyncClient", _RecordingClient):
                with self.assertRaises(ModelGatewayError) as caught:
                    await request_model_candidate(
                        {**BASE_CONFIG, "reasoning_effort": "low"},
                        messages=[{"role": "user", "content": "test"}],
                        stage_name="generation",
                        attempt=1,
                        request_timeout_seconds=1,
                    )
            self.assertEqual(caught.exception.error_code, "MODEL_REASONING_EFFORT_UNSUPPORTED")

        asyncio.run(run())

    def test_missing_sse_done_marker_is_a_contract_failure(self) -> None:
        _RecordingClient.sse_lines = _sse_lines(
            _sse_chunk('{"sql":"SELECT 1","reason":"","assumptions":[]}', finish_reason="stop"),
            done=False,
        )

        with self.assertRaises(ModelGatewayError) as caught:
            self._invoke(BASE_CONFIG)

        self.assertEqual(caught.exception.error_code, "MODEL_RESPONSE_INVALID")

    def test_sse_ignores_heartbeat_and_role_chunks_and_records_usage(self) -> None:
        role_chunk = "data: " + json.dumps(
            {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
        )
        usage_chunk = "data: " + json.dumps(
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 12}}
        )
        _RecordingClient.sse_lines = [
            ": keep-alive",
            "",
            role_chunk,
            "",
            _sse_chunk('{"sql":"SELECT '),
            "",
            usage_chunk,
            "",
            _sse_chunk('1","reason":"","assumptions":[]}', finish_reason="stop"),
            "",
            "data: [DONE]",
            "",
        ]

        result = self._invoke(BASE_CONFIG)

        self.assertEqual(result.candidate.sql, "SELECT 1")
        self.assertEqual(result.record.prompt_tokens, 7)
        self.assertEqual(result.record.completion_tokens, 12)

    def test_stream_error_event_is_mapped_to_upstream_error(self) -> None:
        _RecordingClient.sse_lines = [
            'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":12}}',
            "",
            'data: {"error":{"message":"provider overloaded"}}',
            "",
        ]

        with self.assertRaises(ModelGatewayError) as caught:
            self._invoke(BASE_CONFIG)

        self.assertEqual(caught.exception.error_code, "MODEL_UPSTREAM_ERROR")
        self.assertEqual(caught.exception.status_code, 502)
        assert caught.exception.record is not None
        self.assertEqual(caught.exception.record.prompt_tokens, 7)
        self.assertEqual(caught.exception.record.completion_tokens, 12)

    def test_invalid_sse_event_and_non_stop_finish_reason_are_contract_failures(self) -> None:
        invalid_streams = (
            ["data: {not-json}", ""],
            _sse_lines(
                _sse_chunk('{"sql":"SELECT 1","reason":"","assumptions":[]}', finish_reason="length")
            ),
        )
        for lines in invalid_streams:
            with self.subTest(lines=lines):
                _RecordingClient.sse_lines = lines
                with self.assertRaises(ModelGatewayError) as caught:
                    self._invoke(BASE_CONFIG)
                self.assertEqual(caught.exception.error_code, "MODEL_RESPONSE_INVALID")


if __name__ == "__main__":
    unittest.main()
