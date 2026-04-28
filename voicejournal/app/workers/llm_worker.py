from __future__ import annotations

import threading
from typing import Literal

from PySide6.QtCore import QRunnable

from voicejournal.app.core.llm import LLMEngine, LLMMessage
from voicejournal.app.workers._signals import LLMWorkerSignals


LLMCallType = Literal["question", "prose", "metadata"]


class LLMWorker(QRunnable):
    def __init__(
        self,
        *,
        engine: LLMEngine,
        call_type: LLMCallType,
        messages: list[LLMMessage],
    ) -> None:
        super().__init__()
        self._engine = engine
        self._call_type = call_type
        self._messages = list(messages)
        self._cancel = threading.Event()
        self.signals = LLMWorkerSignals()
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                return

            if self._call_type == "question":
                result = self._engine.question(self._messages)
                if self._cancel.is_set():
                    return
                self.signals.result.emit(result)
                return

            if self._call_type == "prose":
                stream = self._engine.stream_prose(self._messages)
                final = self._emit_stream(stream)
                if self._cancel.is_set():
                    return
                self.signals.result.emit(final)
                return

            if self._call_type == "metadata":
                stream = self._engine.stream_metadata(self._messages)
                final = self._emit_stream(stream)
                if self._cancel.is_set():
                    return
                self.signals.result.emit(self._engine.parse_metadata(final))
                return

            raise ValueError(f"Unsupported LLM call type: {self._call_type}")
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()

    def _emit_stream(self, stream) -> str:
        chunks: list[str] = []
        for chunk in stream:
            if self._cancel.is_set():
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
                return ""
            if not chunk:
                continue
            text = str(chunk)
            chunks.append(text)
            self.signals.chunk.emit(text)
        return "".join(chunks)


__all__ = ["LLMCallType", "LLMWorker"]