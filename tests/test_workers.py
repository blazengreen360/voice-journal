from __future__ import annotations

import io
from pathlib import Path
import threading
import time
from collections.abc import Callable

import numpy as np
import pytest
from PySide6.QtCore import QObject, QRunnable, QThreadPool
from PySide6.QtWidgets import QApplication

from voicejournal.app.core.transcriber import TranscriptionResult
from voicejournal.app.core.llm import MetadataResult, QuestionResult
from voicejournal.app.workers import tts_worker as tts_worker_module
from voicejournal.app.model_sources import ModelSource
from voicejournal.app.workers import (
    LLMLoaderSignals,
    LLMWorkerSignals,
    TTSLoaderSignals,
    TTSWorkerSignals,
    TranscribeWorkerSignals,
    WhisperLoaderSignals,
    WorkerSignals,
)
from voicejournal.app.workers.loaders import LLMLoaderWorker, TTSLoaderWorker, WhisperLoaderWorker
from voicejournal.app.workers.llm_worker import LLMWorker
from voicejournal.app.workers.transcribe_worker import TranscribeWorker
from voicejournal.app.workers.tts_worker import (
    FADE_EDGE_MS,
    FINAL_TAIL_FADE_MS,
    FINAL_TAIL_FADE_POWER,
    PLAYBACK_BLOCK_SIZE,
    TAIL_SILENCE_MS,
    TTSWorker,
    _shape_audio_edges,
    _suppress_final_tail,
)


class _RunnableWorker(QRunnable):
    def __init__(
        self,
        signals: WorkerSignals,
        emit_related: Callable[[WorkerSignals], None],
    ) -> None:
        super().__init__()
        self.signals = signals
        self._emit_related = emit_related
        self._cancel = threading.Event()
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                return
            self._emit_related(self.signals)
            if self._cancel.is_set():
                return
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


def _wait_for_fired(
    app: QApplication,
    pool: QThreadPool,
    fired: list[str],
    expected: list[str],
    timeout_ms: int = 5000,
) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        app.processEvents()
        if fired == expected:
            break
        pool.waitForDone(10)

    pool.waitForDone(timeout_ms)
    for _ in range(5):
        app.processEvents()

    assert fired == expected


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(
    params=[
        pytest.param(
            (
                lambda: _RunnableWorker(
                    TranscribeWorkerSignals(),
                    lambda signals: signals.result.emit({"text": "hello"}),
                ),
                "result",
            ),
            id="transcribe",
        ),
        pytest.param(
            (
                lambda: _RunnableWorker(
                    LLMWorkerSignals(),
                    lambda signals: signals.chunk.emit("chunk"),
                ),
                "chunk",
            ),
            id="llm-chunk",
        ),
        pytest.param(
            (
                lambda: _RunnableWorker(
                    LLMWorkerSignals(),
                    lambda signals: signals.result.emit({"kind": "question"}),
                ),
                "result",
            ),
            id="llm-result",
        ),
        pytest.param(
            (
                lambda: _RunnableWorker(
                    TTSWorkerSignals(),
                    lambda signals: signals.started.emit(),
                ),
                "started",
            ),
            id="tts",
        ),
        pytest.param(
            (
                lambda: _RunnableWorker(
                    WhisperLoaderSignals(),
                    lambda signals: signals.loaded.emit(object()),
                ),
                "loaded",
            ),
            id="whisper-loader",
        ),
        pytest.param(
            (
                lambda: _RunnableWorker(
                    LLMLoaderSignals(),
                    lambda signals: signals.loaded.emit(object()),
                ),
                "loaded",
            ),
            id="llm-loader",
        ),
        pytest.param(
            (
                lambda: _RunnableWorker(
                    TTSLoaderSignals(),
                    lambda signals: signals.loaded.emit(object()),
                ),
                "loaded",
            ),
            id="tts-loader",
        ),
    ]
)
def worker_case(request):
    return request.param


def test_signals_is_qobject(worker_case) -> None:
    worker_factory, _ = worker_case
    worker = worker_factory()

    assert isinstance(worker.signals, QObject)


def test_signal_smoke_fires(qapp: QApplication, worker_case) -> None:
    worker_factory, related_label = worker_case
    worker = worker_factory()
    fired: list[str] = []
    pool = QThreadPool()

    worker.signals.done.connect(lambda: fired.append("done"))
    if isinstance(worker.signals, TranscribeWorkerSignals):
        worker.signals.result.connect(lambda _value: fired.append(related_label))
    elif isinstance(worker.signals, LLMWorkerSignals):
        if related_label == "chunk":
            worker.signals.chunk.connect(lambda _value: fired.append(related_label))
        else:
            worker.signals.result.connect(lambda _value: fired.append(related_label))
    elif isinstance(worker.signals, TTSWorkerSignals):
        worker.signals.started.connect(lambda: fired.append(related_label))
    else:
        worker.signals.loaded.connect(lambda _value: fired.append(related_label))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, [related_label, "done"])

    assert fired.count("done") == 1


def test_done_fires_when_worker_errors(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = _RunnableWorker(
        TTSWorkerSignals(),
        lambda _signals: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])

    assert fired.count("done") == 1


def test_done_fires_when_worker_is_cancelled(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = _RunnableWorker(
        TTSWorkerSignals(),
        lambda signals: signals.started.emit(),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))
    worker.cancel()

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["done"])

    assert fired.count("done") == 1


class _FakeTranscriber:
    def __init__(
        self,
        *,
        result: TranscriptionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result or TranscriptionResult(text="hello")
        self._error = error
        self.calls: list[object] = []

    def transcribe(self, audio) -> TranscriptionResult:
        self.calls.append(audio)
        if self._error is not None:
            raise self._error
        return self._result


def test_real_transcribe_worker_exposes_qobject_signals() -> None:
    worker = TranscribeWorker(
        transcriber=_FakeTranscriber(),
        audio=io.BytesIO(b"RIFF"),
    )

    assert isinstance(worker.signals, QObject)


def test_real_transcribe_worker_emits_result_and_done(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    transcriber = _FakeTranscriber(result=TranscriptionResult(text="hello world"))
    worker = TranscribeWorker(
        transcriber=transcriber,
        audio=io.BytesIO(b"RIFF"),
    )

    worker.signals.result.connect(lambda result: fired.append(result.text))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["hello world", "done"])

    assert len(transcriber.calls) == 1
    assert fired.count("done") == 1


def test_real_transcribe_worker_emits_error_and_done(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = TranscribeWorker(
        transcriber=_FakeTranscriber(error=RuntimeError("boom")),
        audio=io.BytesIO(b"RIFF"),
    )

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])

    assert fired.count("done") == 1


def test_real_transcribe_worker_emits_done_when_cancelled(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    transcriber = _FakeTranscriber()
    worker = TranscribeWorker(
        transcriber=transcriber,
        audio=io.BytesIO(b"RIFF"),
    )

    worker.signals.result.connect(lambda _value: fired.append("result"))
    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))
    worker.cancel()

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["done"])

    assert transcriber.calls == []
    assert fired.count("done") == 1


class _FakeTTSConfig:
    def __init__(self, speech_rate: float = 1.0) -> None:
        self._speech_rate = speech_rate

    @property
    def speech_rate(self) -> float:
        return self._speech_rate


class _FakeTTSEngine:
    def __init__(
        self,
        *,
        samples: np.ndarray | None = None,
        error: Exception | None = None,
        sample_rates: list[int] | None = None,
    ) -> None:
        self._samples = np.asarray(samples if samples is not None else np.linspace(-0.2, 0.2, 2048), dtype=np.float32)
        self._error = error
        self._sample_rates = list(sample_rates or [])
        self.calls: list[dict[str, object]] = []

    def synthesize(self, text: str, *, voice_name: str, speed: float) -> tuple[np.ndarray, int]:
        self.calls.append({"text": text, "voice_name": voice_name, "speed": speed})
        if self._error is not None:
            raise self._error
        sample_rate = self._sample_rates.pop(0) if self._sample_rates else 24_000
        return self._samples.copy(), sample_rate


class _FakeLLMEngine:
    def __init__(
        self,
        *,
        question_result: QuestionResult | None = None,
        prose_chunks: list[str] | None = None,
        metadata_chunks: list[str] | None = None,
        metadata_result: MetadataResult | None = None,
        after_first_prose_chunk: Callable[[], None] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._question_result = question_result or QuestionResult(
            next_question="What happened next?",
            summarize=False,
            summarize_probability=0.2,
        )
        self._prose_chunks = list(["<p>Hello", " world</p>"] if prose_chunks is None else prose_chunks)
        self._metadata_chunks = list(
            ['{"title":"Entry",', '"mood":"Calm",', '"tags":["one"]}']
            if metadata_chunks is None
            else metadata_chunks
        )
        self._metadata_result = metadata_result or MetadataResult(title="Entry", mood="Calm", tags=["one"])
        self._after_first_prose_chunk = after_first_prose_chunk
        self._error = error
        self.calls: list[tuple[str, object]] = []

    def question(self, messages):
        self.calls.append(("question", list(messages)))
        if self._error is not None:
            raise self._error
        return self._question_result

    def stream_prose(self, messages):
        self.calls.append(("prose", list(messages)))
        if self._error is not None:
            raise self._error
        for index, chunk in enumerate(self._prose_chunks):
            if index == 1 and self._after_first_prose_chunk is not None:
                self._after_first_prose_chunk()
            yield chunk

    def stream_metadata(self, messages):
        self.calls.append(("metadata", list(messages)))
        if self._error is not None:
            raise self._error
        yield from self._metadata_chunks

    def parse_metadata(self, raw_text: str):
        self.calls.append(("parse_metadata", raw_text))
        if self._error is not None:
            raise self._error
        return self._metadata_result


class _FakeOutputStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.writes: list[np.ndarray] = []
        self.on_write = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def write(self, data):
        chunk = np.array(data, copy=True)
        self.writes.append(chunk)
        if self.on_write is not None:
            self.on_write(chunk)
        return False


class _FakeOutputStreamFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.streams: list[_FakeOutputStream] = []
        self.on_create_write = None
        self.error: Exception | None = None

    def __call__(self, **kwargs):
        if self.error is not None:
            raise self.error
        self.calls.append(dict(kwargs))
        stream = _FakeOutputStream(**kwargs)
        stream.on_write = self.on_create_write
        self.streams.append(stream)
        return stream


def test_real_tts_worker_exposes_qobject_signals() -> None:
    worker = TTSWorker(
        engine=_FakeTTSEngine(),
        text="hello",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    assert isinstance(worker.signals, QObject)


def test_real_llm_worker_exposes_qobject_signals() -> None:
    worker = LLMWorker(
        engine=_FakeLLMEngine(),
        call_type="question",
        messages=[{"role": "user", "content": "Hello"}],
    )

    assert isinstance(worker.signals, QObject)


def test_real_llm_worker_question_emits_result_and_done(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    engine = _FakeLLMEngine(
        question_result=QuestionResult(
            next_question="What happened after that?",
            summarize=False,
            summarize_probability=0.3,
        )
    )
    worker = LLMWorker(
        engine=engine,
        call_type="question",
        messages=[{"role": "user", "content": "I went for a walk."}],
    )

    worker.signals.result.connect(lambda result: fired.append(result.next_question))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["What happened after that?", "done"])

    assert engine.calls == [("question", [{"role": "user", "content": "I went for a walk."}])]
    assert fired.count("done") == 1


def test_real_llm_worker_prose_streams_chunks_and_final_result(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = LLMWorker(
        engine=_FakeLLMEngine(prose_chunks=["<p>Hello", " world</p>"]),
        call_type="prose",
        messages=[{"role": "user", "content": "Write prose."}],
    )

    worker.signals.chunk.connect(lambda chunk: fired.append(f"chunk:{chunk}"))
    worker.signals.result.connect(lambda result: fired.append(f"result:{result}"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(
        qapp,
        pool,
        fired,
        ["chunk:<p>Hello", "chunk: world</p>", "result:<p>Hello world</p>", "done"],
    )

    assert fired.count("done") == 1


def test_real_llm_worker_metadata_streams_chunks_and_final_result(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = LLMWorker(
        engine=_FakeLLMEngine(
            metadata_chunks=['{"title":"Morning",', '"mood":"Hopeful",', '"tags":["health","habit"]}'],
            metadata_result=MetadataResult(title="Morning", mood="Hopeful", tags=["health", "habit"]),
        ),
        call_type="metadata",
        messages=[{"role": "user", "content": "Create metadata."}],
    )

    worker.signals.chunk.connect(lambda chunk: fired.append(f"chunk:{chunk}"))
    worker.signals.result.connect(lambda result: fired.append(f"result:{result.title}"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(
        qapp,
        pool,
        fired,
        [
            'chunk:{"title":"Morning",',
            'chunk:"mood":"Hopeful",',
            'chunk:"tags":["health","habit"]}',
            "result:Morning",
            "done",
        ],
    )

    assert fired.count("done") == 1


def test_real_llm_worker_emits_error_and_done(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = LLMWorker(
        engine=_FakeLLMEngine(error=RuntimeError("boom")),
        call_type="question",
        messages=[{"role": "user", "content": "Hello"}],
    )

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])

    assert fired.count("done") == 1


def test_real_llm_worker_emits_done_when_cancelled_before_start(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    engine = _FakeLLMEngine()
    worker = LLMWorker(
        engine=engine,
        call_type="question",
        messages=[{"role": "user", "content": "Hello"}],
    )

    worker.signals.result.connect(lambda _value: fired.append("result"))
    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))
    worker.cancel()

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["done"])

    assert engine.calls == []
    assert fired.count("done") == 1


def test_real_llm_worker_stops_streaming_when_cancelled_mid_stream(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker: LLMWorker | None = None

    def cancel_worker() -> None:
        assert worker is not None
        worker.cancel()

    worker = LLMWorker(
        engine=_FakeLLMEngine(
            prose_chunks=["<p>Hello", " world</p>"],
            after_first_prose_chunk=cancel_worker,
        ),
        call_type="prose",
        messages=[{"role": "user", "content": "Write prose."}],
    )

    worker.signals.chunk.connect(lambda chunk: fired.append(f"chunk:{chunk}"))
    worker.signals.result.connect(lambda result: fired.append(f"result:{result}"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["chunk:<p>Hello", "done"])

    assert "result:<p>Hello world</p>" not in fired
    assert fired.count("done") == 1


def test_real_llm_worker_closes_stream_when_cancelled_mid_stream(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker: LLMWorker | None = None

    class _TrackingStream:
        def __init__(self, chunks: list[str], on_before_second: Callable[[], None]) -> None:
            self._chunks = chunks
            self._index = 0
            self._on_before_second = on_before_second
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self) -> str:
            if self._index >= len(self._chunks):
                raise StopIteration
            if self._index == 1:
                self._on_before_second()
            chunk = self._chunks[self._index]
            self._index += 1
            return chunk

        def close(self) -> None:
            self.closed = True

    def cancel_worker() -> None:
        assert worker is not None
        worker.cancel()

    tracking_stream = _TrackingStream(["<p>Hello", " world</p>"], cancel_worker)

    class _TrackingEngine:
        def stream_prose(self, _messages):
            return tracking_stream

    worker = LLMWorker(
        engine=_TrackingEngine(),
        call_type="prose",
        messages=[{"role": "user", "content": "Write prose."}],
    )

    worker.signals.chunk.connect(lambda chunk: fired.append(f"chunk:{chunk}"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["chunk:<p>Hello", "done"])

    assert tracking_stream.closed is True


def test_real_llm_worker_emits_empty_prose_result_on_empty_stream(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = LLMWorker(
        engine=_FakeLLMEngine(prose_chunks=[]),
        call_type="prose",
        messages=[{"role": "user", "content": "Write prose."}],
    )

    worker.signals.result.connect(lambda result: fired.append(f"result:{result}"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["result:", "done"])


def test_real_llm_worker_emits_error_when_metadata_stream_is_empty(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()

    class _EmptyMetadataEngine:
        def stream_metadata(self, _messages):
            return iter(())

        def parse_metadata(self, _raw_text: str):
            raise ValueError("LLM response did not contain a JSON object.")

    worker = LLMWorker(
        engine=_EmptyMetadataEngine(),
        call_type="metadata",
        messages=[{"role": "user", "content": "Create metadata."}],
    )

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])


def test_real_llm_worker_emits_error_for_unsupported_call_type(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = LLMWorker(
        engine=_FakeLLMEngine(),
        call_type="invalid",
        messages=[{"role": "user", "content": "Hello"}],
    )

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])


def test_real_llm_worker_metadata_stops_streaming_when_cancelled_mid_stream(
    qapp: QApplication,
) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker: LLMWorker | None = None

    class _MetadataCancelEngine:
        def stream_metadata(self, _messages):
            yield '{"title":"Entry",'
            assert worker is not None
            worker.cancel()
            yield '"mood":"Calm"}'

        def parse_metadata(self, _raw_text: str):
            return MetadataResult(title="Entry", mood="Calm", tags=[])

    worker = LLMWorker(
        engine=_MetadataCancelEngine(),
        call_type="metadata",
        messages=[{"role": "user", "content": "Create metadata."}],
    )

    worker.signals.chunk.connect(lambda chunk: fired.append(f"chunk:{chunk}"))
    worker.signals.result.connect(lambda result: fired.append(f"result:{result.title}"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ['chunk:{"title":"Entry",', "done"])

    assert "result:Entry" not in fired


def test_real_tts_worker_emits_started_and_done_and_streams_audio(qapp: QApplication, monkeypatch) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    samples = np.linspace(-0.3, 0.3, 14_400, dtype=np.float32)
    engine = _FakeTTSEngine(samples=samples)
    worker = TTSWorker(
        engine=engine,
        text="hello there",
        voice_name="Allen",
        config=_FakeTTSConfig(speech_rate=1.4),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["started", "done"])

    assert engine.calls == [{"text": "hello there", "voice_name": "Allen", "speed": 1.4}]
    assert factory.calls == [{"samplerate": 24_000, "channels": 1, "dtype": "float32"}]
    assert len(factory.streams[0].writes) >= 2
    assert factory.streams[0].writes[0].dtype == np.float32
    assert factory.streams[0].writes[0].shape == (PLAYBACK_BLOCK_SIZE,)
    assert factory.streams[0].writes[0][0] == 0.0
    written_audio = np.concatenate(factory.streams[0].writes)
    tail_samples = int(24_000 * TAIL_SILENCE_MS / 1000)
    edge_samples = min(max(1, int(24_000 * FINAL_TAIL_FADE_MS / 1000)), max(1, samples.size // 2))
    expected_fade_out = samples[-edge_samples:] * np.power(
        np.linspace(1.0, 0.0, edge_samples, dtype=np.float32),
        FINAL_TAIL_FADE_POWER,
    )
    pre_tail_fade_out = written_audio[-tail_samples - edge_samples : -tail_samples]

    assert np.allclose(pre_tail_fade_out, expected_fade_out)
    assert written_audio[-tail_samples:].shape == (tail_samples,)
    assert np.all(written_audio[-tail_samples:] == 0.0)
    assert fired.count("done") == 1


def test_real_tts_worker_appends_tail_silence_only_after_final_streaming_chunk(
    qapp: QApplication, monkeypatch
) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    tail_samples = int(24_000 * TAIL_SILENCE_MS / 1000)
    samples = np.ones(7_200, dtype=np.float32)
    engine = _FakeTTSEngine(samples=samples)
    worker = TTSWorker(
        engine=engine,
        text="First sentence. Middle sentence. Final sentence.",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["started", "done"])

    assert engine.calls == [
        {"text": "First sentence.", "voice_name": "Lucy", "speed": 1.0},
        {"text": "Middle sentence.", "voice_name": "Lucy", "speed": 1.0},
        {"text": "Final sentence.", "voice_name": "Lucy", "speed": 1.0},
    ]
    written_audio = np.concatenate(factory.streams[0].writes)
    first_boundary_window = written_audio[samples.size : samples.size + tail_samples]
    second_boundary_window = written_audio[(samples.size * 2) : (samples.size * 2) + tail_samples]
    final_fade_samples = min(max(1, int(24_000 * FINAL_TAIL_FADE_MS / 1000)), samples.size // 2)
    final_chunk_prefix = written_audio[(samples.size * 2) : (samples.size * 2) + (samples.size - final_fade_samples)]
    final_fade_window = written_audio[(samples.size * 3) - final_fade_samples : (samples.size * 3)]
    expected_final_fade = np.power(
        np.linspace(1.0, 0.0, final_fade_samples, dtype=np.float32),
        FINAL_TAIL_FADE_POWER,
    )

    assert written_audio.shape == ((samples.size * 3) + tail_samples,)
    assert first_boundary_window.shape == (tail_samples,)
    assert second_boundary_window.shape == (tail_samples,)
    assert final_chunk_prefix.shape == (samples.size - final_fade_samples,)
    assert final_fade_window.shape == (final_fade_samples,)
    assert np.all(first_boundary_window == 1.0)
    assert np.all(second_boundary_window == 1.0)
    assert np.all(final_chunk_prefix == 1.0)
    assert np.allclose(final_fade_window, expected_final_fade)
    assert np.all(written_audio[-tail_samples:] == 0.0)
    assert fired.count("done") == 1


def test_suppress_final_tail_caps_fade_to_back_half_of_short_audio() -> None:
    samples = np.ones(10, dtype=np.float32)

    shaped = _suppress_final_tail(samples, 24_000)

    assert np.all(shaped[:5] == 1.0)
    assert shaped[5] == 1.0
    assert np.all(shaped[6:-1] < 1.0)
    assert shaped[-1] == 0.0


def test_short_audio_combined_shaping_and_tail_suppression_stays_finite() -> None:
    samples = np.ones(10, dtype=np.float32)

    shaped = _shape_audio_edges(samples, 24_000, fade_in=True, fade_out=False)
    suppressed = _suppress_final_tail(shaped, 24_000)

    assert np.all(np.isfinite(suppressed))
    assert suppressed.shape == samples.shape
    assert suppressed[-1] == 0.0


def test_real_tts_worker_keeps_comma_clause_in_single_synthesis_call(
    qapp: QApplication, monkeypatch
) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    engine = _FakeTTSEngine()
    text = "I noticed the afternoon shifted, everything felt quieter after that."
    worker = TTSWorker(
        engine=engine,
        text=text,
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["started", "done"])

    assert engine.calls == [{"text": text, "voice_name": "Lucy", "speed": 1.0}]
    assert fired.count("done") == 1


def test_real_tts_worker_errors_when_sample_rate_changes_between_chunks(
    qapp: QApplication, monkeypatch
) -> None:
    fired: list[str] = []
    errors: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    engine = _FakeTTSEngine(sample_rates=[24_000, 22_050])
    worker = TTSWorker(
        engine=engine,
        text="First sentence. Second sentence.",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.error.connect(lambda message: (errors.append(message), fired.append("error")))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["started", "error", "done"])

    assert "TTS sample rate changed between chunks" in errors[0]
    assert engine.calls == [
        {"text": "First sentence.", "voice_name": "Lucy", "speed": 1.0},
        {"text": "Second sentence.", "voice_name": "Lucy", "speed": 1.0},
    ]
    assert fired.count("done") == 1


def test_real_tts_worker_emits_error_and_done(qapp: QApplication, monkeypatch) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    worker = TTSWorker(
        engine=_FakeTTSEngine(error=RuntimeError("boom")),
        text="hello",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])

    assert factory.calls == []
    assert fired.count("done") == 1


def test_real_tts_worker_emits_done_when_cancelled_before_start(qapp: QApplication, monkeypatch) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    engine = _FakeTTSEngine()
    worker = TTSWorker(
        engine=engine,
        text="hello",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))
    worker.cancel()

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["done"])

    assert engine.calls == []
    assert factory.calls == []
    assert fired.count("done") == 1


def test_real_tts_worker_stops_playback_when_cancelled_mid_stream(qapp: QApplication, monkeypatch) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    engine = _FakeTTSEngine(samples=np.linspace(-0.5, 0.5, PLAYBACK_BLOCK_SIZE * 3, dtype=np.float32))
    worker = TTSWorker(
        engine=engine,
        text="hello",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.done.connect(lambda: fired.append("done"))
    factory.on_create_write = lambda _chunk: worker.cancel()

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["started", "done"])

    assert len(factory.streams[0].writes) == 1
    assert fired.count("done") == 1


def test_real_tts_worker_emits_error_and_done_when_output_stream_fails_to_open(
    qapp: QApplication, monkeypatch
) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    factory.error = RuntimeError("open failed")
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    worker = TTSWorker(
        engine=_FakeTTSEngine(),
        text="hello",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["started", "error", "done"])

    assert fired.count("done") == 1


def test_real_tts_worker_emits_error_and_done_when_output_stream_write_fails(
    qapp: QApplication, monkeypatch
) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": factory}))
    worker = TTSWorker(
        engine=_FakeTTSEngine(samples=np.linspace(-0.1, 0.1, PLAYBACK_BLOCK_SIZE + 5, dtype=np.float32)),
        text="hello",
        voice_name="Lucy",
        config=_FakeTTSConfig(),
    )

    original_call = factory.__call__

    def wrapped_call(**kwargs):
        stream = original_call(**kwargs)
        stream.on_write = lambda _chunk: (_ for _ in ()).throw(RuntimeError("write failed"))
        return stream

    monkeypatch.setattr(tts_worker_module, "sd", type("_FakeSD", (), {"OutputStream": wrapped_call}))

    worker.signals.started.connect(lambda: fired.append("started"))
    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["started", "error", "done"])

    assert fired.count("done") == 1


def _loader_source(key: str) -> ModelSource:
    return ModelSource.single_file(
        key=key,
        display=key,
        filename=f"{key}.bin",
        url=f"https://example.test/{key}.bin",
        sha256="0" * 64,
        size_bytes=16,
    )


@pytest.fixture(
    params=[
        pytest.param(
            (
                "whisper-primary",
                lambda load_model: WhisperLoaderWorker(
                    source=_loader_source("whisper-primary"),
                    model_path=Path("/tmp/whisper-primary.bin"),
                    load_model=load_model,
                ),
            ),
            id="whisper-loader-worker",
        ),
        pytest.param(
            (
                "llm-primary",
                lambda load_model: LLMLoaderWorker(
                    primary_source=_loader_source("llm-primary"),
                    primary_path=Path("/tmp/llm-primary.bin"),
                    fallback_source=None,
                    fallback_path=None,
                    load_model=load_model,
                ),
            ),
            id="llm-loader-worker",
        ),
        pytest.param(
            (
                "tts-primary",
                lambda load_model: TTSLoaderWorker(
                    source=_loader_source("tts-primary"),
                    model_path=Path("/tmp/tts-primary.bin"),
                    load_model=load_model,
                ),
            ),
            id="tts-loader-worker",
        ),
    ]
)
def real_loader_worker_case(request):
    return request.param


def test_real_loader_workers_expose_qobject_signals(real_loader_worker_case) -> None:
    _expected_key, build_worker = real_loader_worker_case
    worker = build_worker(lambda source, _path: f"loaded:{source.key}")

    assert isinstance(worker.signals, QObject)


def test_real_loader_workers_emit_loaded_and_done(
    qapp: QApplication, real_loader_worker_case
) -> None:
    expected_key, build_worker = real_loader_worker_case
    fired: list[str] = []
    pool = QThreadPool()
    worker = build_worker(lambda source, _path: f"loaded:{source.key}")

    worker.signals.loaded.connect(lambda loaded: fired.append(loaded.source.key))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, [expected_key, "done"])

    assert fired.count("done") == 1


def test_real_loader_workers_emit_error_and_done(
    qapp: QApplication, real_loader_worker_case
) -> None:
    _expected_key, build_worker = real_loader_worker_case
    fired: list[str] = []
    pool = QThreadPool()
    worker = build_worker(lambda _source, _path: (_ for _ in ()).throw(RuntimeError("boom")))

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])

    assert fired.count("done") == 1


def test_real_llm_loader_worker_falls_back_on_runtime_error(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = LLMLoaderWorker(
        primary_source=_loader_source("llm-primary"),
        primary_path=Path("/tmp/llm-primary.bin"),
        fallback_source=_loader_source("llm-fallback"),
        fallback_path=Path("/tmp/llm-fallback.bin"),
        load_model=lambda source, _path: (
            (_ for _ in ()).throw(RuntimeError("boom"))
            if source.key == "llm-primary"
            else f"loaded:{source.key}"
        ),
    )

    worker.signals.loaded.connect(lambda loaded: fired.append(loaded.source.key))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["llm-fallback", "done"])

    assert fired.count("done") == 1


def test_real_llm_loader_worker_does_not_fall_back_on_missing_file(qapp: QApplication) -> None:
    fired: list[str] = []
    pool = QThreadPool()
    worker = LLMLoaderWorker(
        primary_source=_loader_source("llm-primary"),
        primary_path=Path("/tmp/llm-primary.bin"),
        fallback_source=_loader_source("llm-fallback"),
        fallback_path=Path("/tmp/llm-fallback.bin"),
        load_model=lambda source, _path: (
            (_ for _ in ()).throw(FileNotFoundError(f"missing {source.key}"))
            if source.key == "llm-primary"
            else f"loaded:{source.key}"
        ),
    )

    worker.signals.error.connect(lambda _message: fired.append("error"))
    worker.signals.done.connect(lambda: fired.append("done"))

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["error", "done"])

    assert fired.count("done") == 1


def test_real_loader_workers_emit_done_when_cancelled(
    qapp: QApplication, real_loader_worker_case
) -> None:
    _expected_key, build_worker = real_loader_worker_case
    fired: list[str] = []
    pool = QThreadPool()
    worker = build_worker(lambda source, _path: f"loaded:{source.key}")

    worker.signals.loaded.connect(lambda _loaded: fired.append("loaded"))
    worker.signals.done.connect(lambda: fired.append("done"))
    worker.cancel()

    pool.start(worker)
    _wait_for_fired(qapp, pool, fired, ["done"])

    assert fired.count("done") == 1