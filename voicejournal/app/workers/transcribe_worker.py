from __future__ import annotations

import threading

from PySide6.QtCore import QRunnable

from voicejournal.app.core.transcriber import AudioInput, Transcriber
from voicejournal.app.workers._signals import TranscribeWorkerSignals


class TranscribeWorker(QRunnable):
    def __init__(self, *, transcriber: Transcriber, audio: AudioInput) -> None:
        super().__init__()
        self._transcriber = transcriber
        self._audio = audio
        self._cancel = threading.Event()
        self.signals = TranscribeWorkerSignals()
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                return
            result = self._transcriber.transcribe(self._audio)
            if self._cancel.is_set():
                return
            self.signals.result.emit(result)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


__all__ = ["TranscribeWorker"]