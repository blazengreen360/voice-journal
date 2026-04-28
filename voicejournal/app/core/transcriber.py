from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
from typing import BinaryIO, Callable

import numpy as np

from voicejournal.app.model_sources import ModelSource


TRANSCRIBE_LANGUAGE = "en"
TRANSCRIBE_TASK = "transcribe"
TRANSCRIBE_BEAM_SIZE = 5

AudioInput = BinaryIO | bytes | bytearray | memoryview | np.ndarray


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    language_probability: float | None = None
    duration: float | None = None
    duration_after_vad: float | None = None
    segment_count: int = 0


class Transcriber:
    def __init__(self, model) -> None:
        self._model = model

    @classmethod
    def from_model_path(
        cls,
        model_path: Path,
        *,
        model_factory: Callable[..., object] | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> Transcriber:
        if model_factory is None:
            from faster_whisper import WhisperModel

            model_factory = WhisperModel
        model = model_factory(str(model_path), device=device, compute_type=compute_type)
        return cls(model)

    def transcribe(self, audio: AudioInput) -> TranscriptionResult:
        prepared_audio = self._prepare_audio(audio)
        segments, info = self._model.transcribe(
            prepared_audio,
            language=TRANSCRIBE_LANGUAGE,
            task=TRANSCRIBE_TASK,
            beam_size=TRANSCRIBE_BEAM_SIZE,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
        )
        segment_list = list(segments)
        combined_text = "".join(getattr(segment, "text", "") for segment in segment_list)
        normalized_text = " ".join(combined_text.split())
        return TranscriptionResult(
            text=normalized_text,
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            duration=getattr(info, "duration", None),
            duration_after_vad=getattr(info, "duration_after_vad", None),
            segment_count=len(segment_list),
        )

    def _prepare_audio(self, audio: AudioInput):
        if isinstance(audio, np.ndarray):
            if audio.ndim != 1:
                raise ValueError("NumPy audio must be a one-dimensional waveform.")
            if not np.issubdtype(audio.dtype, np.floating):
                raise ValueError(
                    "NumPy audio must already be a normalized float waveform; "
                    "PCM integer arrays are not supported here."
                )
            waveform = np.asarray(audio, dtype=np.float32)
            if not np.all(np.isfinite(waveform)):
                raise ValueError("NumPy audio must contain only finite float samples.")
            if np.any(np.abs(waveform) > 1.0):
                raise ValueError("NumPy audio must be normalized to the [-1.0, 1.0] range.")
            return waveform

        if isinstance(audio, (bytes, bytearray, memoryview)):
            return io.BytesIO(bytes(audio))

        try:
            audio.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
        return audio


def load_transcriber_model(
    source: ModelSource,
    model_path: Path,
    *,
    model_factory: Callable[..., object] | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
) -> Transcriber:
    if source.install_dir is not None:
        missing = [artifact.filename for artifact in source.artifacts if not (model_path / artifact.filename).is_file()]
        if missing:
            missing_text = ", ".join(missing)
            raise FileNotFoundError(f"{source.display} is not installed; missing {missing_text}")
    elif not model_path.exists():
        raise FileNotFoundError(f"{source.display} is not installed at {model_path}")

    return Transcriber.from_model_path(
        model_path,
        model_factory=model_factory,
        device=device,
        compute_type=compute_type,
    )


__all__ = [
    "AudioInput",
    "TRANSCRIBE_BEAM_SIZE",
    "TRANSCRIBE_LANGUAGE",
    "TRANSCRIBE_TASK",
    "Transcriber",
    "TranscriptionResult",
    "load_transcriber_model",
]