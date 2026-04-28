from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from voicejournal.app.config import AppConfig
from voicejournal.app.core.llm import LLMEngine, load_llm_engine
from voicejournal.app.core.tts import TTSEngine, load_tts_engine
from voicejournal.app.model_sources import (
    DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
    DEFAULT_WRITING_HELP_MODEL_KEY,
    FALLBACK_WRITING_HELP_MODEL_KEY,
    VOICE_REPLY_MODEL_KEY,
    ModelSource,
    model_source,
)
from voicejournal.app.core.transcriber import Transcriber, load_transcriber_model
from voicejournal.app.workers.loaders import (
    LLMLoaderWorker,
    LoadedModel,
    ModelLoader,
    TTSLoaderWorker,
    WhisperLoaderWorker,
)


class ModelRegistry(QObject):
    whisper_ready = Signal()
    llm_ready = Signal()
    llm_fallback = Signal(str, str)
    tts_ready = Signal()
    all_ready = Signal()
    load_error = Signal(str, str)

    def __init__(
        self,
        config: AppConfig,
        *,
        thread_pool: QThreadPool | None = None,
        whisper_loader: ModelLoader | None = None,
        llm_loader: ModelLoader | None = None,
        tts_loader: ModelLoader | None = None,
        llm_model_key: str = DEFAULT_WRITING_HELP_MODEL_KEY,
    ) -> None:
        super().__init__()
        self._config = config
        self._paths = config.paths()
        self._thread_pool = QThreadPool.globalInstance() if thread_pool is None else thread_pool
        self._whisper_loader = whisper_loader or load_transcriber_model
        self._llm_loader = llm_loader or load_llm_engine
        self._tts_loader = tts_loader or load_tts_engine
        self._llm_model_key = llm_model_key

        self._transcriber: Transcriber | None = None
        self._llm: LLMEngine | None = None
        self._tts: TTSEngine | None = None
        self._loaded_llm_key: str | None = None

        self._whisper_loading = False
        self._llm_loading = False
        self._tts_loading = False
        self._active_workers: set[object] = set()

    @property
    def transcriber(self) -> Transcriber | None:
        return self._transcriber

    @property
    def llm(self) -> LLMEngine | None:
        return self._llm

    @property
    def loaded_llm_key(self) -> str | None:
        return self._loaded_llm_key

    @property
    def tts(self) -> TTSEngine | None:
        return self._tts

    @property
    def all_loaded(self) -> bool:
        return self._transcriber is not None and self._llm is not None and self._tts is not None

    def load_all(self) -> None:
        self._submit_whisper_load()
        self._submit_llm_load()
        self._submit_tts_load()

    def ensure_llm_loaded(self) -> bool:
        if self._llm is not None:
            self.llm_ready.emit()
            return True

        self._submit_llm_load()
        return False

    def unload_llm(self) -> None:
        self._llm = None
        self._loaded_llm_key = None

    def _submit_whisper_load(self) -> None:
        if self._transcriber is not None or self._whisper_loading:
            return

        source = model_source(DEFAULT_SPEECH_RECOGNITION_MODEL_KEY)
        worker = WhisperLoaderWorker(
            source=source,
            model_path=self._installed_path(source),
            load_model=self._whisper_loader,
        )
        self._whisper_loading = True
        worker.signals.loaded.connect(self._on_whisper_loaded)
        worker.signals.error.connect(lambda message, display=source.display: self._emit_load_error(display, message))
        worker.signals.done.connect(lambda worker=worker: self._finish_worker(worker, "whisper"))
        self._start_worker(worker)

    def _submit_llm_load(self) -> None:
        if self._llm is not None or self._llm_loading:
            return

        primary_source = model_source(self._llm_model_key)
        fallback_source = (
            None
            if primary_source.key == FALLBACK_WRITING_HELP_MODEL_KEY
            else model_source(FALLBACK_WRITING_HELP_MODEL_KEY)
        )
        worker = LLMLoaderWorker(
            primary_source=primary_source,
            primary_path=self._installed_path(primary_source),
            fallback_source=fallback_source,
            fallback_path=None if fallback_source is None else self._installed_path(fallback_source),
            load_model=self._llm_loader,
        )
        self._llm_loading = True
        worker.signals.loaded.connect(self._on_llm_loaded)
        worker.signals.error.connect(
            lambda message, display=primary_source.display: self._emit_load_error(display, message)
        )
        worker.signals.done.connect(lambda worker=worker: self._finish_worker(worker, "llm"))
        self._start_worker(worker)

    def _submit_tts_load(self) -> None:
        if self._tts is not None or self._tts_loading:
            return

        source = model_source(VOICE_REPLY_MODEL_KEY)
        worker = TTSLoaderWorker(
            source=source,
            model_path=self._installed_path(source),
            load_model=self._tts_loader,
        )
        self._tts_loading = True
        worker.signals.loaded.connect(self._on_tts_loaded)
        worker.signals.error.connect(lambda message, display=source.display: self._emit_load_error(display, message))
        worker.signals.done.connect(lambda worker=worker: self._finish_worker(worker, "tts"))
        self._start_worker(worker)

    def _installed_path(self, source: ModelSource) -> Path:
        if source.install_dir is None:
            return self._paths.models_dir / source.filename
        return self._paths.models_dir / source.install_dir

    def _start_worker(self, worker) -> None:
        self._active_workers.add(worker)
        self._thread_pool.start(worker)

    def _finish_worker(self, worker, worker_kind: str) -> None:
        self._active_workers.discard(worker)
        if worker_kind == "whisper":
            self._whisper_loading = False
        elif worker_kind == "llm":
            self._llm_loading = False
        elif worker_kind == "tts":
            self._tts_loading = False
        else:
            raise ValueError(f"Unsupported worker kind: {worker_kind}")

    def _on_whisper_loaded(self, loaded: LoadedModel) -> None:
        self._transcriber = loaded.model
        self.whisper_ready.emit()
        self._emit_all_ready_if_loaded()

    def _on_llm_loaded(self, loaded: LoadedModel) -> None:
        requested_source = model_source(self._llm_model_key)
        self._llm = loaded.model
        self._loaded_llm_key = loaded.source.key
        if loaded.source.key != requested_source.key:
            self.llm_fallback.emit(requested_source.display, loaded.source.display)
        self.llm_ready.emit()
        self._emit_all_ready_if_loaded()

    def _on_tts_loaded(self, loaded: LoadedModel) -> None:
        self._tts = loaded.model
        self.tts_ready.emit()
        self._emit_all_ready_if_loaded()

    def _emit_all_ready_if_loaded(self) -> None:
        if self.all_loaded:
            self.all_ready.emit()

    def _emit_load_error(self, model_name: str, message: str) -> None:
        self.load_error.emit(model_name, message)


__all__ = ["ModelRegistry"]