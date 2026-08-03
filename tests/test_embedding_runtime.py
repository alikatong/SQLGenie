from __future__ import annotations

import threading

import backend.rag as rag


class _FakeVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return list(self._values)


class _FakeModel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.max_seq_length = 2048

    def encode(self, texts, **_kwargs):
        marker = [1.0] if self.name == "model-a" else [2.0]
        return [_FakeVector(marker) for _ in texts]


def test_concurrent_model_reload_does_not_change_in_flight_model(monkeypatch) -> None:
    runtime = rag._EmbeddingRuntime()
    first_input_prepared = threading.Event()
    second_model_loaded = threading.Event()
    release_first = threading.Event()
    errors: list[BaseException] = []

    original_prepare = rag._prepare_embedding_input

    def make_model(model_name: str, **_kwargs):
        if model_name == "model-b":
            second_model_loaded.set()
        return _FakeModel(model_name)

    def prepare_input(text: str, *, kind: str = "document", model_name: str | None = None) -> str:
        if text == "first":
            first_input_prepared.set()
            if not release_first.wait(timeout=2):
                raise AssertionError("timed out waiting for concurrent model reload")
        return original_prepare(text, kind=kind, model_name=model_name)

    monkeypatch.setattr(rag, "chromadb", object())
    monkeypatch.setattr(rag, "SentenceTransformer", make_model)
    monkeypatch.setattr(rag, "_prepare_embedding_input", prepare_input)

    assert runtime.encode(["warmup"], model_path="model-a") == [[1.0]]

    first_result: list[list[float]] = []
    second_result: list[list[float]] = []

    def run_first() -> None:
        try:
            first_result.extend(runtime.encode(["first"], model_path="model-a"))
        except BaseException as exc:  # pragma: no cover - assertion below reports thread failures
            errors.append(exc)

    def run_second() -> None:
        try:
            second_result.extend(runtime.encode(["second"], model_path="model-b"))
        except BaseException as exc:  # pragma: no cover - assertion below reports thread failures
            errors.append(exc)

    first_thread = threading.Thread(target=run_first)
    second_thread = threading.Thread(target=run_second)
    first_thread.start()
    assert first_input_prepared.wait(timeout=2)
    second_thread.start()
    assert second_model_loaded.wait(timeout=2)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert first_result == [[1.0]]
    assert second_result == [[2.0]]
