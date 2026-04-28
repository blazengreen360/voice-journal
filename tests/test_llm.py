from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import threading
import time

import pytest

from voicejournal.app.core.llm import (
    LLM_CONTEXT_SIZE,
    LLMEngine,
    MetadataResult,
    QUESTION_TEMPERATURE,
    QuestionResult,
    load_llm_engine,
)
from voicejournal.app.model_sources import DEFAULT_WRITING_HELP_MODEL_KEY, model_source


class _FakeLlama:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses: list[object] = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("No fake llama response queued")
        return self.responses.pop(0)


def _chat_response(content: str) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


def _stream_response(*chunks: str) -> Iterator[dict[str, object]]:
    for chunk in chunks:
        yield {
            "choices": [
                {
                    "delta": {"content": chunk} if chunk else {},
                }
            ]
        }


def test_llm_engine_question_uses_json_mode_and_parses_response() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _chat_response(
            '{"next_question": "What happened after that?", '
            '"summarize": false, "summarize_probability": 0.82}'
        )
    )
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "I felt overwhelmed today."}])

    assert result == QuestionResult(
        next_question="What happened after that?",
        summarize=False,
        summarize_probability=0.82,
    )
    assert len(llama.calls) == 1
    call = llama.calls[0]
    assert call["messages"][0]["role"] == "system"
    assert "follow-up question assistant" in call["messages"][0]["content"]
    assert "warmly conversational" in call["messages"][0]["content"]
    assert "Avoid generic repeats" in call["messages"][0]["content"]
    assert call["messages"][1:] == [{"role": "user", "content": "I felt overwhelmed today."}]
    assert call["response_format"] == {"type": "json_object"}
    assert call["temperature"] == QUESTION_TEMPERATURE
    assert call["max_tokens"] == 96
    assert call["stream"] is False


def test_llm_engine_stream_prose_yields_delta_content() -> None:
    llama = _FakeLlama()
    llama.responses.append(_stream_response("<p>Hello", " world</p>", ""))
    engine = LLMEngine(llama)

    chunks = list(engine.stream_prose([{"role": "user", "content": "Write my journal entry."}]))

    assert chunks == ["<p>Hello", " world</p>"]
    assert "journal summarizer" in llama.calls[0]["messages"][0]["content"]
    assert "Do not critique" in llama.calls[0]["messages"][0]["content"]
    assert llama.calls[0]["temperature"] == 0.2
    assert llama.calls[0]["stream"] is True
    assert "response_format" not in llama.calls[0]


def test_llm_engine_stream_metadata_and_parse_metadata() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _stream_response('{"title":"A steady morning",', '"mood":"Reflective",', '"tags":["health","routine"]}')
    )
    engine = LLMEngine(llama)

    raw = "".join(engine.stream_metadata([{"role": "user", "content": "Create metadata."}]))
    metadata = engine.parse_metadata(raw)

    assert metadata == MetadataResult(
        title="A steady morning",
        mood="Reflective",
        tags=["health", "routine"],
    )
    assert llama.calls[0]["response_format"] == {"type": "json_object"}
    assert llama.calls[0]["stream"] is True


def test_load_llm_engine_uses_context_size_and_quiet_mode(tmp_path: Path) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_text("gguf")
    source = model_source(DEFAULT_WRITING_HELP_MODEL_KEY)
    captured: dict[str, object] = {}

    def llama_factory(path: str, *, n_ctx: int, verbose: bool):
        captured["path"] = path
        captured["n_ctx"] = n_ctx
        captured["verbose"] = verbose
        return _FakeLlama()

    engine = load_llm_engine(source, model_path, llama_factory=llama_factory)

    assert isinstance(engine, LLMEngine)
    assert captured == {
        "path": str(model_path),
        "n_ctx": LLM_CONTEXT_SIZE,
        "verbose": False,
    }


def test_llm_engine_question_preserves_summarize_hint_on_malformed_json() -> None:
    llama = _FakeLlama()
    llama.responses.append(_chat_response('summarize {"next_question": "Wrap up soon"'))
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "I think I am finished."}])

    assert result == QuestionResult(next_question="", summarize=True, summarize_probability=None)


def test_llm_engine_question_preserves_true_summarize_on_truncated_json() -> None:
    llama = _FakeLlama()
    llama.responses.append(_chat_response('{"next_question": "Wrap up soon", "summarize": true'))
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "I think I am finished."}])

    assert result == QuestionResult(next_question="", summarize=True, summarize_probability=None)


def test_llm_engine_question_coerces_string_false_to_false() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _chat_response(
            '{"next_question": "Tell me more about that.", '
            '"summarize": "false", "summarize_probability": "0.25"}'
        )
    )
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "I am not done yet."}])

    assert result == QuestionResult(
        next_question="Tell me more about that.",
        summarize=False,
        summarize_probability=0.25,
    )


def test_llm_engine_question_ignores_invalid_probability() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _chat_response(
            '{"next_question": "Tell me more about that.", '
            '"summarize": false, "summarize_probability": "not-a-number"}'
        )
    )
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "Keep going."}])

    assert result == QuestionResult(
        next_question="Tell me more about that.",
        summarize=False,
        summarize_probability=None,
    )


def test_llm_engine_question_does_not_treat_json_string_content_as_summarize_hint() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _chat_response(
            '{"next_question": "You mentioned summarize: true feelings earlier.", '
            '"summarize": false}'
        )
    )
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "Keep going."}])

    assert result == QuestionResult(
        next_question="You mentioned summarize: true feelings earlier.",
        summarize=False,
        summarize_probability=None,
    )


def test_llm_engine_question_ignores_plain_trailing_summarize_text_when_json_is_false() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _chat_response(
            '{"next_question": "Keep going.", "summarize": false} '
            'Note: I will not summarize yet.'
        )
    )
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "Keep going."}])

    assert result == QuestionResult(
        next_question="Keep going.",
        summarize=False,
        summarize_probability=None,
    )


def test_llm_engine_question_ignores_trailing_summarize_true_when_json_is_false() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _chat_response(
            '{"next_question": "Keep going.", "summarize": false} '
            'Note: summarize: true'
        )
    )
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "Keep going."}])

    assert result == QuestionResult(
        next_question="Keep going.",
        summarize=False,
        summarize_probability=None,
    )


def test_llm_engine_question_ignores_pre_json_preamble_when_json_is_false() -> None:
    llama = _FakeLlama()
    llama.responses.append(
        _chat_response(
            'I should not summarize yet. '
            '{"next_question": "Keep going.", "summarize": false}'
        )
    )
    engine = LLMEngine(llama)

    result = engine.question([{"role": "user", "content": "Keep going."}])

    assert result == QuestionResult(
        next_question="Keep going.",
        summarize=False,
        summarize_probability=None,
    )


def test_load_llm_engine_requires_model_file(tmp_path: Path) -> None:
    source = model_source(DEFAULT_WRITING_HELP_MODEL_KEY)

    with pytest.raises(FileNotFoundError, match="not installed"):
        load_llm_engine(source, tmp_path / "missing.gguf")


def test_llm_engine_serializes_concurrent_calls() -> None:
    class _ConcurrentFakeLlama:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def create_chat_completion(self, **kwargs):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            if kwargs.get("stream"):
                return _stream_response("<p>Hello</p>")
            return _chat_response('{"next_question":"What next?","summarize":false}')

    llama = _ConcurrentFakeLlama()
    engine = LLMEngine(llama)
    threads = [
        threading.Thread(target=lambda: list(engine.stream_prose([{"role": "user", "content": "Write prose"}]))),
        threading.Thread(target=lambda: engine.question([{"role": "user", "content": "Ask"}])),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert llama.max_active == 1