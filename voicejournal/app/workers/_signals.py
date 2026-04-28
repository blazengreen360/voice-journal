from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class WorkerSignals(QObject):
    done = Signal()
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class TranscribeWorkerSignals(WorkerSignals):
    result = Signal(object)


class LLMWorkerSignals(WorkerSignals):
    chunk = Signal(str)
    result = Signal(object)


class TTSWorkerSignals(WorkerSignals):
    started = Signal()


class LoaderWorkerSignals(WorkerSignals):
    loaded = Signal(object)


class WhisperLoaderSignals(LoaderWorkerSignals):
    pass


class LLMLoaderSignals(LoaderWorkerSignals):
    pass


class TTSLoaderSignals(LoaderWorkerSignals):
    pass


__all__ = [
    "WorkerSignals",
    "TranscribeWorkerSignals",
    "LLMWorkerSignals",
    "TTSWorkerSignals",
    "LoaderWorkerSignals",
    "WhisperLoaderSignals",
    "LLMLoaderSignals",
    "TTSLoaderSignals",
]