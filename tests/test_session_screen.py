from __future__ import annotations

from dataclasses import dataclass
import io
import threading
import time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QApplication

from voicejournal.app.core.llm import QuestionResult
from voicejournal.app.core.transcriber import TranscriptionResult
from voicejournal.app.ui.session_screen import (
    DEFAULT_FOLLOW_UP_QUESTION,
    OPENING_PROMPT,
    SESSION_PROMPT_HINT,
    SESSION_VAD_PARAMS,
    SessionScreen,
)


class _FakeLLM:
    def __init__(self, responses: list[QuestionResult]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def question(self, messages):
        self.calls.append(list(messages))
        return self._responses.pop(0)


class _FakeVADWorker:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_for_new_session(self) -> None:
        self.reset_calls += 1


@dataclass(frozen=True, slots=True)
class _FakeAudioConfig:
    audio_device: int | str | None = None


class _FakeTranscriber:
    def __init__(self, *, text: str) -> None:
        self._text = text
        self.calls: list[object] = []

    def transcribe(self, audio) -> TranscriptionResult:
        self.calls.append(audio)
        return TranscriptionResult(text=self._text)


class _BlockingTranscriber:
    def __init__(self, *, text: str) -> None:
        self._text = text
        self.calls: list[object] = []
        self.release = threading.Event()

    def transcribe(self, audio) -> TranscriptionResult:
        self.calls.append(audio)
        self.release.wait(timeout=1)
        return TranscriptionResult(text=self._text)


class _FakeTTSEngine:
    def synthesize(self, text: str, *, voice_name: str, speed: float = 1.0):
        return text, voice_name, speed


class _FakeTTSWorkerSignals(QObject):
    started = Signal()
    error = Signal(str)
    done = Signal()


class _BlockingTTSWorker(QRunnable):
    def __init__(
        self,
        *,
        text: str,
        voice_name: str,
        call_log: list[dict[str, str]],
        release: threading.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.signals = _FakeTTSWorkerSignals()
        self._cancel = threading.Event()
        self._text = text
        self._voice_name = voice_name
        self._call_log = call_log
        self.release = release or threading.Event()
        self._error = error
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel.set()
        self.release.set()

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                return
            self._call_log.append({"text": self._text, "voice_name": self._voice_name})
            self.signals.started.emit()
            if self._error is not None:
                raise self._error
            self.release.wait(timeout=1)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


class _FakeAudioStream:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _FakeLiveVADWorker:
    def __init__(self, on_speech_end, on_error=None) -> None:
        self._on_speech_end = on_speech_end
        self._on_error = on_error
        self.started = False
        self.stopped = False
        self.joined = False
        self.reset_calls = 0
        self.muted_values: list[bool] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_alive(self) -> bool:
        return self.started and not self.joined

    def join(self, timeout=None) -> None:
        if not self.started:
            raise RuntimeError("cannot join thread before it is started")
        del timeout
        self.joined = True

    def reset_for_new_session(self) -> None:
        self.reset_calls += 1

    def set_muted(self, muted: bool) -> None:
        self.muted_values.append(muted)

    def emit_segment(self, segment: io.BytesIO) -> None:
        self._on_speech_end(segment)

    def emit_error(self, message: str) -> None:
        if self._on_error is not None:
            self._on_error(RuntimeError(message))


class _BlockingLLM:
    def __init__(self) -> None:
        self.release = threading.Event()

    def question(self, _messages):
        self.release.wait(timeout=1)
        return QuestionResult(next_question="Stale follow-up", summarize=False)


class _SlowLLM:
    def __init__(self) -> None:
        self.release = threading.Event()

    def question(self, _messages):
        self.release.wait(timeout=1)
        return QuestionResult(next_question="What happened next?", summarize=False)


def _wait_until(app: QApplication, predicate, timeout_ms: int = 4000) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
    assert predicate()


def test_session_screen_starts_clean_and_requests_follow_up() -> None:
    app = QApplication.instance() or QApplication([])
    vad_worker = _FakeVADWorker()
    llm = _FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)])
    screen = SessionScreen(
        llm_engine=llm,
        thread_pool=QThreadPool(),
        vad_worker=vad_worker,
    )

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("It felt quiet when I got home.")
        screen.submit_user_turn()

        _wait_until(app, lambda: screen.turn_input.isEnabled() and "What happened next?" in screen.transcript_view.toPlainText())

        assert vad_worker.reset_calls == 1
        assert screen.prompt_label.text() == SESSION_PROMPT_HINT
        assert screen.finish_button.text() == "Finish Entry"
        assert screen.add_photo_button.text() == "Add Photo"
        assert screen.active_session is not None
        assert screen.active_session.user_turn_count() == 1
        assert OPENING_PROMPT in screen.transcript_view.toPlainText()
        assert llm.calls[0][:2] == [
            {"role": "assistant", "content": OPENING_PROMPT},
            {"role": "user", "content": "It felt quiet when I got home."},
        ]
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_uses_conversational_vad_timing() -> None:
    app = QApplication.instance() or QApplication([])
    captured_params = []

    def make_audio_stream(_config, _vad_queue):
        return _FakeAudioStream()

    def make_vad_worker(_vad_queue, on_speech_end, params, _session, **_kwargs):
        del on_speech_end
        captured_params.append(params)
        return _FakeLiveVADWorker(lambda _segment: None)

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=make_audio_stream,
        vad_worker_factory=make_vad_worker,
        vad_session_factory=lambda _path: object(),
    )

    try:
        screen.start_session(entry_id="entry-1")

        assert captured_params == [SESSION_VAD_PARAMS]
        assert captured_params[0].min_silence_duration_ms == 650
        assert captured_params[0].speech_pad_ms == 120
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_uses_warmer_fallback_question_when_llm_is_unavailable() -> None:
    app = QApplication.instance() or QApplication([])
    screen = SessionScreen(llm_engine=None, thread_pool=QThreadPool())

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("It felt quiet when I got home.")
        screen.submit_user_turn()

        assert DEFAULT_FOLLOW_UP_QUESTION in screen.transcript_view.toPlainText()
        assert screen.active_session is not None
        assert screen.active_session.turns[-1] == {"role": "assistant", "text": DEFAULT_FOLLOW_UP_QUESTION}
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_uses_warmer_fallback_question_when_llm_errors() -> None:
    app = QApplication.instance() or QApplication([])

    class _ErrorLLM:
        def question(self, _messages):
            raise RuntimeError("llm failed")

    screen = SessionScreen(llm_engine=_ErrorLLM(), thread_pool=QThreadPool())

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("It felt quiet when I got home.")
        screen.submit_user_turn()

        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and DEFAULT_FOLLOW_UP_QUESTION in screen.transcript_view.toPlainText(),
        )

        assert screen.active_session is not None
        assert screen.active_session.turns[-1] == {"role": "assistant", "text": DEFAULT_FOLLOW_UP_QUESTION}
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_only_honors_summarize_after_three_user_turns() -> None:
    app = QApplication.instance() or QApplication([])
    screen = SessionScreen(
        llm_engine=_FakeLLM(
            [
                QuestionResult(next_question="Tell me more.", summarize=True, summarize_probability=0.9),
                QuestionResult(next_question="Keep going.", summarize=True, summarize_probability=0.9),
                QuestionResult(next_question="You can finish when ready.", summarize=True, summarize_probability=0.9),
            ]
        ),
        thread_pool=QThreadPool(),
    )

    try:
        screen.start_session(entry_id="entry-1")
        for index, text in enumerate(("One", "Two", "Three"), start=1):
            screen.turn_input.setText(text)
            screen.submit_user_turn()
            _wait_until(app, lambda: screen.turn_input.isEnabled())
            if index < 3:
                assert screen.can_honor_summarize is False

        assert screen.can_honor_summarize is True
        assert "last turn or two" in screen.turn_label.text()
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_ignores_stale_question_result_after_restart() -> None:
    app = QApplication.instance() or QApplication([])
    llm = _BlockingLLM()
    screen = SessionScreen(llm_engine=llm, thread_pool=QThreadPool())

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("First turn")
        screen.submit_user_turn()
        screen.start_session(entry_id="entry-2")
        llm.release.set()

        _wait_until(app, lambda: screen.active_session is not None and screen.active_session.entry_id == "entry-2")

        assert "Stale follow-up" not in screen.transcript_view.toPlainText()
        assert screen.active_session is not None
        assert screen.active_session.entry_id == "entry-2"
        assert screen.active_session.turns == [{"role": "assistant", "text": OPENING_PROMPT}]
        assert screen.turn_input.isEnabled() is True
        assert screen.submit_turn_button.isEnabled() is True
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_disables_finish_button_while_thinking() -> None:
    app = QApplication.instance() or QApplication([])
    llm = _SlowLLM()
    screen = SessionScreen(llm_engine=llm, thread_pool=QThreadPool())

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("First turn")
        screen.submit_user_turn()

        assert screen.finish_button.isEnabled() is False

        llm.release.set()
        _wait_until(app, lambda: screen.turn_input.isEnabled())

        assert screen.finish_button.isEnabled() is True
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_transcribes_spoken_turn_and_stops_voice_runtime_on_finish() -> None:
    app = QApplication.instance() or QApplication([])
    audio_streams: list[_FakeAudioStream] = []
    vad_workers: list[_FakeLiveVADWorker] = []

    def make_audio_stream(_config, _vad_queue):
        stream = _FakeAudioStream()
        audio_streams.append(stream)
        return stream

    def make_vad_worker(_vad_queue, on_speech_end, _params, _session, **_kwargs):
        worker = _FakeLiveVADWorker(on_speech_end)
        vad_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        transcriber=_FakeTranscriber(text="It felt quiet when I got home."),
        thread_pool=QThreadPool(),
        audio_stream_factory=make_audio_stream,
        vad_worker_factory=make_vad_worker,
        vad_session_factory=lambda _path: object(),
    )

    try:
        screen.start_session(entry_id="entry-1")

        assert audio_streams[0].started is True
        assert vad_workers[0].started is True

        vad_workers[0].emit_segment(io.BytesIO(b"RIFF"))
        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and "It felt quiet when I got home." in screen.transcript_view.toPlainText()
            and "What happened next?" in screen.transcript_view.toPlainText(),
        )

        assert screen.active_session is not None
        assert screen.active_session.user_turn_count() == 1

        screen.finish_entry()

        assert audio_streams[0].stopped is True
        assert vad_workers[0].stopped is True
        assert vad_workers[0].joined is True
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_falls_back_to_typed_turn_when_mic_start_fails() -> None:
    app = QApplication.instance() or QApplication([])

    def fail_audio_stream(_config, _vad_queue):
        raise RuntimeError("mic failed")

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=fail_audio_stream,
        vad_worker_factory=lambda *_args, **_kwargs: _FakeLiveVADWorker(lambda _segment: None),
        vad_session_factory=lambda _path: object(),
    )

    try:
        screen.start_session(entry_id="entry-1")

        assert screen.state_label.text() == "Mic unavailable. Type what happened…"

        screen.turn_input.setText("Typed fallback")
        screen.submit_user_turn()
        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and "Typed fallback" in screen.transcript_view.toPlainText()
            and "What happened next?" in screen.transcript_view.toPlainText(),
        )

        assert screen.active_session is not None
        assert screen.active_session.user_turn_count() == 1
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_ignores_stale_transcription_after_restart() -> None:
    app = QApplication.instance() or QApplication([])
    audio_streams: list[_FakeAudioStream] = []
    vad_workers: list[_FakeLiveVADWorker] = []
    transcriber = _BlockingTranscriber(text="Stale transcribed turn")

    def make_audio_stream(_config, _vad_queue):
        stream = _FakeAudioStream()
        audio_streams.append(stream)
        return stream

    def make_vad_worker(_vad_queue, on_speech_end, _params, _session, **_kwargs):
        worker = _FakeLiveVADWorker(on_speech_end)
        vad_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        transcriber=transcriber,
        thread_pool=QThreadPool(),
        audio_stream_factory=make_audio_stream,
        vad_worker_factory=make_vad_worker,
        vad_session_factory=lambda _path: object(),
    )

    try:
        screen.start_session(entry_id="entry-1")
        vad_workers[0].emit_segment(io.BytesIO(b"RIFF"))
        _wait_until(app, lambda: screen.transcribe_in_flight is True)

        screen.start_session(entry_id="entry-2")
        transcriber.release.set()

        _wait_until(
            app,
            lambda: screen.active_session is not None
            and screen.active_session.entry_id == "entry-2"
            and screen.turn_input.isEnabled(),
        )

        assert screen.active_session is not None
        assert screen.active_session.entry_id == "entry-2"
        assert screen.active_session.turns == [{"role": "assistant", "text": OPENING_PROMPT}]
        assert "Stale transcribed turn" not in screen.transcript_view.toPlainText()
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_recovers_to_typed_fallback_after_vad_runtime_error() -> None:
    app = QApplication.instance() or QApplication([])
    vad_workers: list[_FakeLiveVADWorker] = []

    def make_audio_stream(_config, _vad_queue):
        return _FakeAudioStream()

    def make_vad_worker(_vad_queue, on_speech_end, _params, _session, **kwargs):
        worker = _FakeLiveVADWorker(on_speech_end, on_error=kwargs.get("on_error"))
        vad_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=make_audio_stream,
        vad_worker_factory=make_vad_worker,
        vad_session_factory=lambda _path: object(),
    )

    try:
        screen.start_session(entry_id="entry-1")
        vad_workers[0].emit_error("session exploded")

        _wait_until(
            app,
            lambda: screen.state_label.text() == "Mic unavailable. Type what happened…"
            and screen.turn_input.isEnabled(),
        )

        screen.turn_input.setText("Typed after error")
        screen.submit_user_turn()
        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and "Typed after error" in screen.transcript_view.toPlainText()
            and "What happened next?" in screen.transcript_view.toPlainText(),
        )
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_speaks_assistant_turn_and_restores_listening_after_playback() -> None:
    app = QApplication.instance() or QApplication([])
    tts_calls: list[dict[str, str]] = []
    tts_workers: list[_BlockingTTSWorker] = []
    vad_workers: list[_FakeLiveVADWorker] = []

    def make_audio_stream(_config, _vad_queue):
        return _FakeAudioStream()

    def make_vad_worker(_vad_queue, on_speech_end, _params, _session, **_kwargs):
        worker = _FakeLiveVADWorker(on_speech_end)
        vad_workers.append(worker)
        return worker

    def make_tts_worker(*, engine, text, voice_name, config):
        del engine, config
        worker = _BlockingTTSWorker(text=text, voice_name=voice_name, call_log=tts_calls)
        tts_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        tts_engine=_FakeTTSEngine(),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=make_audio_stream,
        vad_worker_factory=make_vad_worker,
        vad_session_factory=lambda _path: object(),
        tts_worker_factory=make_tts_worker,
    )

    try:
        screen.start_session(entry_id="entry-1")

        _wait_until(
            app,
            lambda: len(tts_workers) == 1 and len(tts_calls) == 1 and screen.tts_in_flight is True,
        )
        assert tts_calls == [{"text": OPENING_PROMPT, "voice_name": "Lucy"}]
        assert screen.turn_input.isEnabled() is False
        assert True in vad_workers[0].muted_values

        tts_workers[0].release.set()
        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and screen.state_label.text() == "Listening…"
            and screen.tts_in_flight is False,
            timeout_ms=5000,
        )

        screen.turn_input.setText("First turn")
        screen.submit_user_turn()

        _wait_until(
            app,
            lambda: len(tts_workers) == 2
            and len(tts_calls) == 2
            and screen.tts_in_flight is True
            and screen.state_label.text() == "Speaking · mic off",
        )

        assert screen.turn_input.isEnabled() is False
        assert tts_calls[-1] == {"text": "What happened next?", "voice_name": "Lucy"}

        tts_workers[1].release.set()
        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and screen.state_label.text() == "Listening…"
            and screen.tts_in_flight is False,
            timeout_ms=5000,
        )

        assert vad_workers[0].muted_values[-1] is False
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_ignores_vad_segments_while_assistant_is_speaking() -> None:
    app = QApplication.instance() or QApplication([])
    tts_calls: list[dict[str, str]] = []
    tts_workers: list[_BlockingTTSWorker] = []
    vad_workers: list[_FakeLiveVADWorker] = []
    transcriber = _FakeTranscriber(text="Leaked turn")

    def make_audio_stream(_config, _vad_queue):
        return _FakeAudioStream()

    def make_vad_worker(_vad_queue, on_speech_end, _params, _session, **_kwargs):
        worker = _FakeLiveVADWorker(on_speech_end)
        vad_workers.append(worker)
        return worker

    def make_tts_worker(*, engine, text, voice_name, config):
        del engine, config
        worker = _BlockingTTSWorker(text=text, voice_name=voice_name, call_log=tts_calls)
        tts_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        tts_engine=_FakeTTSEngine(),
        transcriber=transcriber,
        thread_pool=QThreadPool(),
        audio_stream_factory=make_audio_stream,
        vad_worker_factory=make_vad_worker,
        vad_session_factory=lambda _path: object(),
        tts_worker_factory=make_tts_worker,
    )

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("First turn")
        screen.submit_user_turn()
        _wait_until(app, lambda: screen.tts_in_flight is True)

        vad_workers[0].emit_segment(io.BytesIO(b"RIFF"))
        for _ in range(5):
            app.processEvents()

        assert transcriber.calls == []
        assert "Leaked turn" not in screen.transcript_view.toPlainText()

        tts_workers[0].release.set()
        _wait_until(app, lambda: screen.turn_input.isEnabled(), timeout_ms=5000)
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_surfaces_tts_failure_and_recovers_listening_state() -> None:
    app = QApplication.instance() or QApplication([])
    tts_calls: list[dict[str, str]] = []
    vad_workers: list[_FakeLiveVADWorker] = []

    def make_tts_worker(*, engine, text, voice_name, config):
        del engine, config
        return _BlockingTTSWorker(
            text=text,
            voice_name=voice_name,
            call_log=tts_calls,
            error=RuntimeError("speaker failed"),
        )

    def make_vad_worker(_vad_queue, on_speech_end, _params, _session, **_kwargs):
        worker = _FakeLiveVADWorker(on_speech_end)
        vad_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        tts_engine=_FakeTTSEngine(),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=lambda _config, _vad_queue: _FakeAudioStream(),
        vad_worker_factory=make_vad_worker,
        vad_session_factory=lambda _path: object(),
        tts_worker_factory=make_tts_worker,
    )

    try:
        screen.start_session(entry_id="entry-1")

        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and len(tts_calls) == 1
            and screen.tts_in_flight is False
            and screen.state_label.text() == "Voice unavailable. Listening…",
            timeout_ms=5000,
        )
        assert tts_calls == [{"text": OPENING_PROMPT, "voice_name": "Lucy"}]

        screen.turn_input.setText("First turn")
        screen.submit_user_turn()

        _wait_until(
            app,
            lambda: screen.turn_input.isEnabled()
            and screen.tts_in_flight is False
            and screen.state_label.text() == "Voice unavailable. Listening…",
            timeout_ms=5000,
        )

        assert tts_calls[-1] == {"text": "What happened next?", "voice_name": "Lucy"}
        assert vad_workers[0].muted_values[-1] is False
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_finish_entry_stops_pending_tts_release_timer() -> None:
    app = QApplication.instance() or QApplication([])
    tts_calls: list[dict[str, str]] = []
    tts_workers: list[_BlockingTTSWorker] = []
    finished: list[object] = []

    def make_tts_worker(*, engine, text, voice_name, config):
        del engine, config
        worker = _BlockingTTSWorker(text=text, voice_name=voice_name, call_log=tts_calls)
        tts_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        tts_engine=_FakeTTSEngine(),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=lambda _config, _vad_queue: _FakeAudioStream(),
        vad_worker_factory=lambda _vad_queue, on_speech_end, _params, _session, **_kwargs: _FakeLiveVADWorker(on_speech_end),
        vad_session_factory=lambda _path: object(),
        tts_worker_factory=make_tts_worker,
    )
    screen.finish_requested.connect(lambda session, _photos: finished.append(session))

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("First turn")
        screen.submit_user_turn()
        _wait_until(app, lambda: screen.tts_in_flight is True)

        tts_workers[0].release.set()
        _wait_until(
            app,
            lambda: screen.tts_in_flight is False and screen.turn_input.isEnabled() is False,
        )

        screen.finish_entry()
        _wait_until(app, lambda: len(finished) == 1)

        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            app.processEvents()

        assert screen.turn_input.isEnabled() is False
    finally:
        screen.close()
        app.processEvents()


def test_session_screen_ignores_stale_tts_completion_after_restart() -> None:
    app = QApplication.instance() or QApplication([])
    tts_calls: list[dict[str, str]] = []
    tts_workers: list[_BlockingTTSWorker] = []

    def make_tts_worker(*, engine, text, voice_name, config):
        del engine, config
        worker = _BlockingTTSWorker(text=text, voice_name=voice_name, call_log=tts_calls)
        tts_workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        tts_engine=_FakeTTSEngine(),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=lambda _config, _vad_queue: _FakeAudioStream(),
        vad_worker_factory=lambda _vad_queue, on_speech_end, _params, _session, **_kwargs: _FakeLiveVADWorker(on_speech_end),
        vad_session_factory=lambda _path: object(),
        tts_worker_factory=make_tts_worker,
    )

    try:
        screen.start_session(entry_id="entry-1")
        screen.turn_input.setText("First turn")
        screen.submit_user_turn()
        _wait_until(app, lambda: screen.tts_in_flight is True)

        screen.start_session(entry_id="entry-2")
        tts_workers[0].release.set()

        _wait_until(
            app,
            lambda: screen.active_session is not None
            and screen.active_session.entry_id == "entry-2"
            and screen.turn_input.isEnabled()
            and screen.tts_in_flight is False,
            timeout_ms=5000,
        )

        assert screen.active_session is not None
        assert screen.active_session.entry_id == "entry-2"
        assert screen.active_session.turns == [{"role": "assistant", "text": OPENING_PROMPT}]
        assert screen.state_label.text() == "Listening…"
    finally:
        screen.close()
        app.processEvents()

def test_session_screen_defers_assistant_text_until_tts_starts() -> None:
    app = QApplication.instance() or QApplication([])
    tts_calls: list[dict[str, str]] = []

    class _StartGatedTTSWorker(QRunnable):
        def __init__(self, *, text: str, voice_name: str) -> None:
            super().__init__()
            self.signals = _FakeTTSWorkerSignals()
            self._text = text
            self._voice_name = voice_name
            self.start_release = threading.Event()
            self.finish_release = threading.Event()
            self.setAutoDelete(False)

        def cancel(self) -> None:
            self.start_release.set()
            self.finish_release.set()

        def run(self) -> None:
            try:
                tts_calls.append({"text": self._text, "voice_name": self._voice_name})
                self.start_release.wait(timeout=2)
                self.signals.started.emit()
                self.finish_release.wait(timeout=2)
            finally:
                self.signals.done.emit()

    workers: list[_StartGatedTTSWorker] = []

    def make_tts_worker(*, engine, text, voice_name, config):
        del engine, config
        worker = _StartGatedTTSWorker(text=text, voice_name=voice_name)
        workers.append(worker)
        return worker

    screen = SessionScreen(
        config=_FakeAudioConfig(),
        llm_engine=_FakeLLM([QuestionResult(next_question="What happened next?", summarize=False)]),
        tts_engine=_FakeTTSEngine(),
        transcriber=_FakeTranscriber(text="unused"),
        thread_pool=QThreadPool(),
        audio_stream_factory=lambda _config, _vad_queue: _FakeAudioStream(),
        vad_worker_factory=lambda _vad_queue, on_speech_end, _params, _session, **_kwargs: _FakeLiveVADWorker(on_speech_end),
        vad_session_factory=lambda _path: object(),
        tts_worker_factory=make_tts_worker,
    )

    try:
        screen.start_session(entry_id="entry-1")

        _wait_until(app, lambda: len(workers) == 1 and len(tts_calls) == 1)
        assert tts_calls[0] == {"text": OPENING_PROMPT, "voice_name": "Lucy"}
        workers[0].start_release.set()
        workers[0].finish_release.set()
        _wait_until(app, lambda: screen.turn_input.isEnabled() and screen.tts_in_flight is False)

        screen.turn_input.setText("First turn")
        screen.submit_user_turn()

        # Wait for the LLM question to settle and the TTS worker to be queued.
        _wait_until(app, lambda: len(workers) == 2 and len(tts_calls) == 2)
        # `started` has NOT been emitted yet — assistant text must be hidden.
        for _ in range(5):
            app.processEvents()
        assert "What happened next?" not in screen.transcript_view.toPlainText()

        # Release `started`; transcript should reveal the line.
        workers[1].start_release.set()
        _wait_until(
            app,
            lambda: "What happened next?" in screen.transcript_view.toPlainText(),
        )

        workers[1].finish_release.set()
        _wait_until(app, lambda: screen.turn_input.isEnabled() and screen.tts_in_flight is False)
    finally:
        screen.close()
        app.processEvents()
