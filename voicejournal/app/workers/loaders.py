from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import threading

from PySide6.QtCore import QRunnable

from voicejournal.app.model_sources import ModelSource
from voicejournal.app.workers._signals import (
    LLMLoaderSignals,
    TTSLoaderSignals,
    WhisperLoaderSignals,
)


ModelLoader = Callable[[ModelSource, Path], object]


@dataclass(frozen=True, slots=True)
class LoadedModel:
    source: ModelSource
    model: object


class _BaseLoaderWorker(QRunnable):
    def __init__(
        self,
        *,
        source: ModelSource,
        model_path: Path,
        load_model: ModelLoader,
        signals,
    ) -> None:
        super().__init__()
        self._source = source
        self._model_path = model_path
        self._load_model = load_model
        self._cancel = threading.Event()
        self.signals = signals
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel.set()

    def _run_loader(self) -> LoadedModel:
        model = self._load_model(self._source, self._model_path)
        return LoadedModel(source=self._source, model=model)

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                return
            loaded = self._run_loader()
            if self._cancel.is_set():
                return
            self.signals.loaded.emit(loaded)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


class WhisperLoaderWorker(_BaseLoaderWorker):
    def __init__(self, *, source: ModelSource, model_path: Path, load_model: ModelLoader) -> None:
        super().__init__(
            source=source,
            model_path=model_path,
            load_model=load_model,
            signals=WhisperLoaderSignals(),
        )


class LLMLoaderWorker(_BaseLoaderWorker):
    def __init__(
        self,
        *,
        primary_source: ModelSource,
        primary_path: Path,
        fallback_source: ModelSource | None,
        fallback_path: Path | None,
        load_model: ModelLoader,
    ) -> None:
        super().__init__(
            source=primary_source,
            model_path=primary_path,
            load_model=load_model,
            signals=LLMLoaderSignals(),
        )
        self._fallback_source = fallback_source
        self._fallback_path = fallback_path

    def _run_loader(self) -> LoadedModel:
        try:
            return super()._run_loader()
        except RuntimeError as primary_error:
            # Missing files and other setup errors must surface to the user.
            # Only runtime model-load failures are eligible for Gemma fallback.
            if self._fallback_source is None or self._fallback_path is None:
                raise
            try:
                model = self._load_model(self._fallback_source, self._fallback_path)
            except Exception as fallback_error:
                raise RuntimeError(
                    f"{self._source.display} failed to load: {primary_error}; "
                    f"{self._fallback_source.display} also failed: {fallback_error}"
                ) from fallback_error
            return LoadedModel(source=self._fallback_source, model=model)


class TTSLoaderWorker(_BaseLoaderWorker):
    def __init__(self, *, source: ModelSource, model_path: Path, load_model: ModelLoader) -> None:
        super().__init__(
            source=source,
            model_path=model_path,
            load_model=load_model,
            signals=TTSLoaderSignals(),
        )


__all__ = [
    "LoadedModel",
    "LLMLoaderWorker",
    "ModelLoader",
    "TTSLoaderWorker",
    "WhisperLoaderWorker",
]