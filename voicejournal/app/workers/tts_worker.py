from __future__ import annotations

import re
import threading

import numpy as np
from PySide6.QtCore import QRunnable
import sounddevice as sd

from voicejournal.app.config import AppConfig
from voicejournal.app.core.tts import TTSEngine
from voicejournal.app.workers._signals import TTSWorkerSignals


PLAYBACK_BLOCK_SIZE = 1024
FADE_EDGE_MS = 18
FINAL_TAIL_FADE_MS = 240
FINAL_TAIL_FADE_POWER = 3.0
TAIL_SILENCE_MS = 100

# Split on sentence-ending punctuation while keeping the punctuation with the
# preceding sentence. Kokoro already does its own punctuation-aware batching,
# so splitting earlier on clause punctuation tends to hurt prosody more than it
# helps startup time.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_for_streaming(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks = [chunk.strip() for chunk in _SENTENCE_SPLIT.split(cleaned) if chunk.strip()]
    return chunks or [cleaned]


def _shape_audio_edges(
    samples: np.ndarray,
    sample_rate: int,
    *,
    fade_in: bool = True,
    fade_out: bool = True,
) -> np.ndarray:
    audio = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    if audio.size < 2 or sample_rate <= 0:
        return audio
    if not fade_in and not fade_out:
        return audio

    edge_samples = max(1, int(sample_rate * FADE_EDGE_MS / 1000))
    edge_limit = audio.size // 2 if fade_in and fade_out else audio.size
    edge_samples = min(edge_samples, edge_limit)

    shaped = audio.copy()
    if fade_in:
        fade = np.linspace(0.0, 1.0, edge_samples, dtype=np.float32)
        shaped[:edge_samples] *= fade
    if fade_out:
        fade = np.linspace(1.0, 0.0, edge_samples, dtype=np.float32)
        shaped[-edge_samples:] *= fade
    return np.ascontiguousarray(shaped, dtype=np.float32)


def _append_tail_silence(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0 or sample_rate <= 0:
        return audio

    tail_samples = max(1, int(sample_rate * TAIL_SILENCE_MS / 1000))
    tail = np.zeros(tail_samples, dtype=np.float32)
    return np.ascontiguousarray(np.concatenate([audio, tail]), dtype=np.float32)


def _suppress_final_tail(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    if audio.size < 2 or sample_rate <= 0:
        return audio

    fade_samples = max(1, int(sample_rate * FINAL_TAIL_FADE_MS / 1000))
    fade_samples = min(fade_samples, max(1, audio.size // 2))

    shaped = audio.copy()
    fade = np.power(
        np.linspace(1.0, 0.0, fade_samples, dtype=np.float32),
        FINAL_TAIL_FADE_POWER,
    )
    shaped[-fade_samples:] *= fade
    return np.ascontiguousarray(shaped, dtype=np.float32)


class TTSWorker(QRunnable):
    def __init__(
        self,
        *,
        engine: TTSEngine,
        text: str,
        voice_name: str,
        config: AppConfig,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._text = text
        self._voice_name = voice_name
        self._config = config
        self._cancel = threading.Event()
        self.signals = TTSWorkerSignals()
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        out = None
        output_sample_rate: int | None = None
        try:
            if self._cancel.is_set():
                return

            chunks = _split_for_streaming(self._text)
            if not chunks:
                return

            speed = self._config.speech_rate
            for index, chunk in enumerate(chunks):
                if self._cancel.is_set():
                    break

                samples, sample_rate = self._engine.synthesize(
                    chunk,
                    voice_name=self._voice_name,
                    speed=speed,
                )
                if self._cancel.is_set():
                    break

                is_final_chunk = index == len(chunks) - 1
                audio = _shape_audio_edges(
                    samples,
                    sample_rate,
                    fade_in=index == 0,
                    fade_out=False,
                )
                if is_final_chunk:
                    audio = _suppress_final_tail(audio, sample_rate)
                    audio = _append_tail_silence(audio, sample_rate)
                if audio.size == 0:
                    continue

                if out is None:
                    output_sample_rate = sample_rate
                    self.signals.started.emit()
                    stream = sd.OutputStream(
                        samplerate=sample_rate,
                        channels=1,
                        dtype="float32",
                    )
                    stream.__enter__()
                    out = stream
                elif sample_rate != output_sample_rate:
                    raise RuntimeError(
                        "TTS sample rate changed between chunks: "
                        f"{output_sample_rate} -> {sample_rate}"
                    )

                cursor = 0
                while cursor < audio.size:
                    if self._cancel.is_set():
                        break
                    end = min(cursor + PLAYBACK_BLOCK_SIZE, audio.size)
                    out.write(np.ascontiguousarray(audio[cursor:end], dtype=np.float32))
                    cursor = end

        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            if out is not None:
                try:
                    out.__exit__(None, None, None)
                except Exception:
                    pass
            self.signals.done.emit()


__all__ = [
    "FADE_EDGE_MS",
    "FINAL_TAIL_FADE_MS",
    "FINAL_TAIL_FADE_POWER",
    "PLAYBACK_BLOCK_SIZE",
    "TAIL_SILENCE_MS",
    "TTSWorker",
]