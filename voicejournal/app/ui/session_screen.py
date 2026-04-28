from __future__ import annotations

from collections.abc import Callable
from importlib.resources import as_file
from html import escape
from pathlib import Path
import queue
from uuid import uuid4

import onnxruntime as ort
from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from voicejournal.app.bundled_assets import bundled_silero_vad_model
from voicejournal.app.config import AppConfig
from voicejournal.app.core.audio import AudioStream
from voicejournal.app.core.llm import LLMEngine, LLMMessage, QuestionResult
from voicejournal.app.core.tts import TTSEngine
from voicejournal.app.core.transcriber import Transcriber, TranscriptionResult
from voicejournal.app.core.vad import VADParams, VADWorker
from voicejournal.app.models.entry import ActiveSession
from voicejournal.app.workers.llm_worker import LLMWorker
from voicejournal.app.workers.transcribe_worker import TranscribeWorker
from voicejournal.app.workers.tts_worker import TTSWorker


OPENING_PROMPT = "What did today feel like for you?"
DEFAULT_FOLLOW_UP_QUESTION = "What part of that stands out most to you?"
SESSION_VAD_PARAMS = VADParams(min_silence_duration_ms=650, speech_pad_ms=120)
SESSION_PROMPT_HINT = "Take your time. You can pause and keep going."


class SessionScreen(QWidget):
    cancel_requested = Signal()
    finish_requested = Signal(object, object)
    speech_segment_detected = Signal(object, int)
    voice_error = Signal(str, int)

    def __init__(
        self,
        *,
        llm_engine: LLMEngine | None = None,
        tts_engine: TTSEngine | None = None,
        transcriber: Transcriber | None = None,
        config: AppConfig | None = None,
        thread_pool: QThreadPool | None = None,
        vad_worker=None,
        audio_stream_factory: Callable[[AppConfig, queue.Queue], object] | None = None,
        vad_worker_factory: Callable[..., object] | None = None,
        vad_session_factory: Callable[[str], object] | None = None,
        tts_worker_factory: Callable[..., object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._llm_engine = llm_engine
        self._tts_engine = tts_engine
        self._transcriber = transcriber
        self._config = config
        self._thread_pool = QThreadPool.globalInstance() if thread_pool is None else thread_pool
        self._external_vad_worker = vad_worker
        self._audio_stream_factory = AudioStream if audio_stream_factory is None else audio_stream_factory
        self._vad_worker_factory = VADWorker if vad_worker_factory is None else vad_worker_factory
        self._vad_session_factory = ort.InferenceSession if vad_session_factory is None else vad_session_factory
        self._tts_worker_factory = TTSWorker if tts_worker_factory is None else tts_worker_factory
        self._session: ActiveSession | None = None
        self._question_worker: LLMWorker | None = None
        self._transcribe_worker: TranscribeWorker | None = None
        self._tts_worker: object | None = None
        self._question_in_flight = False
        self._transcribe_in_flight = False
        self._tts_in_flight = False
        self._can_honor_summarize = False
        self._photo_paths: list[Path] = []
        self._session_token = 0
        self._audio_stream = None
        self._voice_vad_worker = None
        self._vad_queue: queue.Queue | None = None
        self._vad_session = None
        self._vad_model_path_context = None
        self._voice_status_message = "Type what happened…"
        self._playback_status_message: str | None = None
        self._tts_release_session_token = 0
        self._pending_assistant_text: str | None = None

        self._tts_release_timer = QTimer(self)
        self._tts_release_timer.setSingleShot(True)
        self._tts_release_timer.setInterval(300)
        self._tts_release_timer.timeout.connect(self._on_tts_release_timeout)

        top_bar = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.turn_label = QLabel("Turn 1", self)
        self.voice_selector = QComboBox(self)
        self.voice_selector.addItems(["Lucy", "Allen"])
        self.voice_selector.currentTextChanged.connect(self._on_voice_changed)
        top_bar.addWidget(self.cancel_button)
        top_bar.addStretch(1)
        top_bar.addWidget(self.turn_label)
        top_bar.addStretch(1)
        top_bar.addWidget(self.voice_selector)

        self.prompt_label = QLabel(SESSION_PROMPT_HINT, self)
        self.state_label = QLabel("Listening…", self)
        self.transcript_view = QTextBrowser(self)

        input_row = QHBoxLayout()
        self.turn_input = QLineEdit(self)
        self.turn_input.setPlaceholderText("Type or speak what happened…")
        self.turn_input.returnPressed.connect(self.submit_user_turn)
        self.submit_turn_button = QPushButton("Continue", self)
        self.submit_turn_button.clicked.connect(self.submit_user_turn)
        input_row.addWidget(self.turn_input)
        input_row.addWidget(self.submit_turn_button)

        action_row = QHBoxLayout()
        self.add_photo_button = QPushButton("Add Photo", self)
        self.add_photo_button.clicked.connect(self._pick_photos)
        self.finish_button = QPushButton("Finish Entry", self)
        self.finish_button.clicked.connect(self.finish_entry)
        action_row.addWidget(self.add_photo_button)
        action_row.addStretch(1)
        action_row.addWidget(self.finish_button)

        self.photo_summary_label = QLabel("No photos added yet.", self)

        layout = QVBoxLayout(self)
        layout.addLayout(top_bar)
        layout.addWidget(self.prompt_label)
        layout.addWidget(self.state_label)
        layout.addWidget(self.transcript_view)
        layout.addLayout(input_row)
        layout.addLayout(action_row)
        layout.addWidget(self.photo_summary_label)
        self.setLayout(layout)

        self.speech_segment_detected.connect(self._on_speech_segment_ready)
        self.voice_error.connect(self._on_voice_runtime_error)

        self._set_state("LISTENING")
        self.finish_button.setEnabled(False)

    @property
    def active_session(self) -> ActiveSession | None:
        return self._session

    @property
    def can_honor_summarize(self) -> bool:
        return self._can_honor_summarize

    @property
    def question_in_flight(self) -> bool:
        return self._question_in_flight

    @property
    def transcribe_in_flight(self) -> bool:
        return self._transcribe_in_flight

    @property
    def tts_in_flight(self) -> bool:
        return self._tts_in_flight

    def start_session(self, *, entry_id: str | None = None, voice_name: str = "Lucy") -> None:
        self._cancel_workers()
        self._stop_voice_runtime()
        self._tts_release_timer.stop()

        self._session_token += 1

        self._session = ActiveSession(entry_id=entry_id or str(uuid4()), voice_name=voice_name)
        self.voice_selector.setCurrentText(voice_name)
        self.transcript_view.clear()
        self.turn_input.clear()
        self.turn_input.setEnabled(True)
        self.submit_turn_button.setEnabled(True)
        self.photo_summary_label.setText("No photos added yet.")
        self._photo_paths = []
        self._question_in_flight = False
        self._transcribe_in_flight = False
        self._tts_in_flight = False
        self._can_honor_summarize = False
        self._playback_status_message = None
        self._pending_assistant_text = None
        self._tts_release_session_token = self._session_token
        self._update_turn_label()
        self.finish_button.setEnabled(False)

        if self._external_vad_worker is not None and hasattr(self._external_vad_worker, "reset_for_new_session"):
            self._external_vad_worker.reset_for_new_session()

        self._start_voice_runtime()
        if self._start_opening_prompt():
            self.turn_input.setEnabled(False)
            self.submit_turn_button.setEnabled(False)
        else:
            self._set_ready_for_input_state()

    def add_photo_paths(self, paths: list[Path]) -> None:
        for path in paths:
            path_obj = Path(path)
            if path_obj not in self._photo_paths:
                self._photo_paths.append(path_obj)
        count = len(self._photo_paths)
        suffix = "photo" if count == 1 else "photos"
        self.photo_summary_label.setText(f"{count} {suffix} ready for review.")

    def selected_photo_paths(self) -> list[Path]:
        return list(self._photo_paths)

    def set_llm_engine(self, llm_engine: LLMEngine | None) -> None:
        self._llm_engine = llm_engine

    def set_tts_engine(self, tts_engine: TTSEngine | None) -> None:
        self._tts_engine = tts_engine

    def set_transcriber(self, transcriber: Transcriber | None) -> None:
        self._transcriber = transcriber

    def submit_user_turn(self) -> None:
        if self._session is None or self._question_in_flight or self._transcribe_in_flight or self._tts_in_flight:
            return

        text = self.turn_input.text().strip()
        if not text:
            return

        self.turn_input.clear()
        self._submit_user_turn_text(text)

    def finish_entry(self) -> None:
        if self._session is None or self._question_in_flight or self._transcribe_in_flight or self._tts_in_flight:
            return
        self._tts_release_timer.stop()
        self._stop_voice_runtime()
        self.finish_requested.emit(self._session.snapshot(), self.selected_photo_paths())

    def _append_assistant_question(self, result: QuestionResult) -> None:
        if self._session is None:
            return

        self._can_honor_summarize = result.summarize and self._session.user_turn_count() >= 3
        next_question = result.next_question.strip() or DEFAULT_FOLLOW_UP_QUESTION
        self._session.add_turn("assistant", next_question)
        self._update_turn_label(result.summarize_probability)
        scheduled = self._start_assistant_speech(next_question)
        if not scheduled:
            self._append_turn("VoiceJournal", next_question)

    def _on_question_result(self, worker: LLMWorker, session_token: int, result: QuestionResult) -> None:
        if worker is not self._question_worker or session_token != self._session_token:
            return
        self._append_assistant_question(result)

    def _on_question_error(self, worker: LLMWorker, session_token: int, _message: str) -> None:
        if worker is not self._question_worker or session_token != self._session_token:
            return
        if self._session is None:
            return
        fallback = QuestionResult(next_question=DEFAULT_FOLLOW_UP_QUESTION, summarize=False)
        self._append_assistant_question(fallback)

    def _on_question_done(self, worker: LLMWorker, session_token: int) -> None:
        if worker is not self._question_worker or session_token != self._session_token:
            return
        self._question_in_flight = False
        self._question_worker = None
        self._sync_voice_capture_state()
        if not self._transcribe_in_flight and not self._tts_in_flight:
            self.turn_input.setEnabled(True)
            self.submit_turn_button.setEnabled(True)
            self.finish_button.setEnabled(self._session is not None and self._session.user_turn_count() > 0)
            self._set_ready_for_input_state()

    def _on_tts_started(self, worker, session_token: int) -> None:
        if worker is not self._tts_worker or session_token != self._session_token:
            return
        self._reveal_pending_assistant_text()
        self._set_state("SPEAKING")
        self._sync_voice_capture_state()

    def _on_tts_error(self, worker, session_token: int, _message: str) -> None:
        if worker is not self._tts_worker or session_token != self._session_token:
            return
        self._reveal_pending_assistant_text()
        self._playback_status_message = "Voice unavailable. Listening…"

    def _on_tts_done(self, worker, session_token: int) -> None:
        if worker is not self._tts_worker or session_token != self._session_token:
            return
        self._reveal_pending_assistant_text()
        self._tts_in_flight = False
        self._tts_worker = None
        if self._question_in_flight or self._transcribe_in_flight:
            self._sync_voice_capture_state()
            return
        self._tts_release_session_token = session_token
        self._tts_release_timer.start()

    def _on_tts_release_timeout(self) -> None:
        if self._tts_release_session_token != self._session_token:
            return
        if self._question_in_flight or self._transcribe_in_flight or self._tts_in_flight:
            return
        self._sync_voice_capture_state()
        self.turn_input.setEnabled(True)
        self.submit_turn_button.setEnabled(True)
        self.finish_button.setEnabled(self._session is not None and self._session.user_turn_count() > 0)
        self._set_ready_for_input_state()

    def _on_speech_segment_ready(self, audio, session_token: int) -> None:
        if session_token != self._session_token or self._session is None:
            return
        if self._question_in_flight or self._transcribe_in_flight or self._tts_in_flight:
            return
        if self._transcriber is None or not hasattr(self._transcriber, "transcribe"):
            return

        worker = TranscribeWorker(transcriber=self._transcriber, audio=audio)
        self._transcribe_in_flight = True
        self._transcribe_worker = worker
        self._set_state("PROCESSING")
        self.turn_input.setEnabled(False)
        self.submit_turn_button.setEnabled(False)
        self.finish_button.setEnabled(False)
        self._sync_voice_capture_state()

        worker.signals.result.connect(
            lambda result, worker=worker, token=session_token: self._on_transcribe_result(worker, token, result)
        )
        worker.signals.error.connect(
            lambda message, worker=worker, token=session_token: self._on_transcribe_error(worker, token, message)
        )
        worker.signals.done.connect(
            lambda worker=worker, token=session_token: self._on_transcribe_done(worker, token)
        )
        self._thread_pool.start(worker)

    def _on_transcribe_result(self, worker: TranscribeWorker, session_token: int, result: TranscriptionResult) -> None:
        if worker is not self._transcribe_worker or session_token != self._session_token:
            return
        text = result.text.strip()
        if text:
            self._submit_user_turn_text(text)

    def _on_transcribe_error(self, worker: TranscribeWorker, session_token: int, _message: str) -> None:
        if worker is not self._transcribe_worker or session_token != self._session_token:
            return

    def _on_transcribe_done(self, worker: TranscribeWorker, session_token: int) -> None:
        if worker is not self._transcribe_worker or session_token != self._session_token:
            return
        self._transcribe_in_flight = False
        self._transcribe_worker = None
        self._sync_voice_capture_state()
        if not self._question_in_flight:
            self.turn_input.setEnabled(True)
            self.submit_turn_button.setEnabled(True)
            self.finish_button.setEnabled(self._session is not None and self._session.user_turn_count() > 0)
            self._set_ready_for_input_state()

    def _submit_user_turn_text(self, text: str) -> None:
        assert self._session is not None

        self._session.add_turn("user", text)
        self._append_turn("You", text)
        self.finish_button.setEnabled(True)

        if self._llm_engine is None:
            self._append_assistant_question(
                QuestionResult(next_question=DEFAULT_FOLLOW_UP_QUESTION, summarize=False)
            )
            return

        self._question_in_flight = True
        self._set_state("THINKING")
        self.turn_input.setEnabled(False)
        self.submit_turn_button.setEnabled(False)
        self.finish_button.setEnabled(False)
        self._sync_voice_capture_state()

        worker = LLMWorker(
            engine=self._llm_engine,
            call_type="question",
            messages=self._prompt_messages(),
        )
        session_token = self._session_token
        self._question_worker = worker
        worker.signals.result.connect(
            lambda result, worker=worker, token=session_token: self._on_question_result(worker, token, result)
        )
        worker.signals.error.connect(
            lambda message, worker=worker, token=session_token: self._on_question_error(worker, token, message)
        )
        worker.signals.done.connect(lambda worker=worker, token=session_token: self._on_question_done(worker, token))
        self._thread_pool.start(worker)

    def _start_assistant_speech(self, text: str) -> bool:
        if self._tts_engine is None or self._config is None or self._session is None:
            return False
        if not hasattr(self._tts_engine, "synthesize"):
            return False

        self._playback_status_message = None
        self._pending_assistant_text = text
        worker = self._tts_worker_factory(
            engine=self._tts_engine,
            text=text,
            voice_name=self._session.voice_name,
            config=self._config,
        )
        session_token = self._session_token
        self._tts_in_flight = True
        self._tts_worker = worker
        self._set_state("SPEAKING")
        self._sync_voice_capture_state()
        worker.signals.started.connect(
            lambda worker=worker, token=session_token: self._on_tts_started(worker, token)
        )
        worker.signals.error.connect(
            lambda message, worker=worker, token=session_token: self._on_tts_error(worker, token, message)
        )
        worker.signals.done.connect(
            lambda worker=worker, token=session_token: self._on_tts_done(worker, token)
        )
        self._thread_pool.start(worker)
        return True

    def _start_opening_prompt(self) -> bool:
        if self._session is None:
            return False
        self._session.add_turn("assistant", OPENING_PROMPT)
        scheduled = self._start_assistant_speech(OPENING_PROMPT)
        if not scheduled:
            self._append_turn("VoiceJournal", OPENING_PROMPT)
        return scheduled

    def _reveal_pending_assistant_text(self) -> None:
        text = self._pending_assistant_text
        if text is None:
            return
        self._pending_assistant_text = None
        self._append_turn("VoiceJournal", text)

    def _start_voice_runtime(self) -> None:
        self._voice_status_message = "Type what happened…"
        if self._config is None or self._transcriber is None or not hasattr(self._transcriber, "transcribe"):
            return

        try:
            vad_session = self._ensure_vad_session()
            self._vad_queue = queue.Queue()
            session_token = self._session_token
            self._voice_vad_worker = self._vad_worker_factory(
                self._vad_queue,
                lambda segment, token=session_token: self.speech_segment_detected.emit(segment, token),
                SESSION_VAD_PARAMS,
                vad_session,
                on_error=lambda exc, token=session_token: self.voice_error.emit(str(exc), token),
                poll_interval=0.01,
            )
            self._audio_stream = self._audio_stream_factory(self._config, self._vad_queue)
            if hasattr(self._voice_vad_worker, "start"):
                self._voice_vad_worker.start()
            self._audio_stream.start()
            self._sync_voice_capture_state()
        except Exception:
            self._stop_voice_runtime()
            self._voice_status_message = "Mic unavailable. Type what happened…"

    def _stop_voice_runtime(self) -> None:
        if self._audio_stream is not None:
            try:
                self._audio_stream.stop()
            except Exception:
                pass
            self._audio_stream = None

        if self._voice_vad_worker is not None:
            worker_was_alive = False
            if hasattr(self._voice_vad_worker, "is_alive"):
                try:
                    worker_was_alive = bool(self._voice_vad_worker.is_alive())
                except Exception:
                    worker_was_alive = False
            if hasattr(self._voice_vad_worker, "stop"):
                self._voice_vad_worker.stop()
            if worker_was_alive and hasattr(self._voice_vad_worker, "join"):
                try:
                    self._voice_vad_worker.join(timeout=0.05)
                except TypeError:
                    self._voice_vad_worker.join()
                except RuntimeError:
                    pass
            self._voice_vad_worker = None

        self._vad_queue = None

    def _ensure_vad_session(self):
        if self._vad_session is not None:
            return self._vad_session

        if self._vad_model_path_context is None:
            self._vad_model_path_context = as_file(bundled_silero_vad_model())
            self._vad_model_path = self._vad_model_path_context.__enter__()

        self._vad_session = self._vad_session_factory(str(self._vad_model_path))
        return self._vad_session

    def _sync_voice_capture_state(self) -> None:
        if self._voice_vad_worker is not None and hasattr(self._voice_vad_worker, "set_muted"):
            self._voice_vad_worker.set_muted(
                self._question_in_flight or self._transcribe_in_flight or self._tts_in_flight
            )

    def _set_ready_for_input_state(self) -> None:
        if self._tts_in_flight:
            self._set_state("SPEAKING")
            return
        if self._playback_status_message is not None:
            if self._voice_vad_worker is not None and self._audio_stream is not None:
                self.turn_input.setPlaceholderText("Type or speak what happened…")
            else:
                self.turn_input.setPlaceholderText("Type what happened…")
            self.state_label.setText(self._playback_status_message)
            return
        if self._voice_vad_worker is not None and self._audio_stream is not None:
            self.turn_input.setPlaceholderText("Type or speak what happened…")
            self._set_state("LISTENING")
            return

        self.turn_input.setPlaceholderText("Type what happened…")
        self.state_label.setText(self._voice_status_message)

    def _on_voice_runtime_error(self, _message: str, session_token: int) -> None:
        if session_token != self._session_token:
            return
        self._voice_status_message = "Mic unavailable. Type what happened…"
        self._stop_voice_runtime()
        if not self._question_in_flight and not self._transcribe_in_flight and not self._tts_in_flight:
            self._set_ready_for_input_state()

    def _request_cancel(self) -> None:
        self._cancel_workers()
        self._question_in_flight = False
        self._transcribe_in_flight = False
        self._tts_in_flight = False
        self._tts_release_timer.stop()
        self._stop_voice_runtime()
        self.cancel_requested.emit()

    def _cancel_workers(self) -> None:
        if self._question_worker is not None:
            self._question_worker.cancel()
            self._question_worker = None
        if self._transcribe_worker is not None:
            self._transcribe_worker.cancel()
            self._transcribe_worker = None
        if self._tts_worker is not None:
            self._tts_worker.cancel()
            self._tts_worker = None
        self._question_in_flight = False
        self._transcribe_in_flight = False
        self._tts_in_flight = False
        self._tts_release_timer.stop()

    def _append_turn(self, speaker: str, text: str) -> None:
        self.transcript_view.append(f"<p><strong>{escape(speaker)}</strong> · {escape(text)}</p>")

    def _prompt_messages(self) -> list[LLMMessage]:
        assert self._session is not None
        return [{"role": turn["role"], "content": turn["text"]} for turn in self._session.turns]

    def _update_turn_label(self, summarize_probability: float | None = None) -> None:
        turn_number = 1 if self._session is None else self._session.user_turn_count() + 1
        text = f"Turn {turn_number}"
        if summarize_probability is not None:
            if summarize_probability >= 0.85:
                text += " · last turn or two"
            else:
                low = max(1, round((1.0 - summarize_probability) * 2))
                high = low + 1
                text += f" · about {low}-{high} to go"
        self.turn_label.setText(text)

    def _set_state(self, state: str) -> None:
        if state == "THINKING":
            self.state_label.setText("Thinking…")
        elif state == "PROCESSING":
            self.state_label.setText("Transcribing…")
        elif state == "SPEAKING":
            self.state_label.setText("Speaking · mic off")
        else:
            self.state_label.setText("Listening…")

    def _pick_photos(self) -> None:
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Photo",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        if file_paths:
            self.add_photo_paths([Path(path) for path in file_paths])

    def _on_voice_changed(self, voice_name: str) -> None:
        if self._session is not None:
            self._session.voice_name = voice_name

    def closeEvent(self, event) -> None:
        self._cancel_workers()
        self._stop_voice_runtime()
        if self._vad_model_path_context is not None:
            self._vad_model_path_context.__exit__(None, None, None)
            self._vad_model_path_context = None
        super().closeEvent(event)


__all__ = [
    "DEFAULT_FOLLOW_UP_QUESTION",
    "OPENING_PROMPT",
    "SESSION_PROMPT_HINT",
    "SESSION_VAD_PARAMS",
    "SessionScreen",
]