from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pytest

from voicejournal.app.core.transcriber import (
    TRANSCRIBE_BEAM_SIZE,
    TRANSCRIBE_LANGUAGE,
    TRANSCRIBE_TASK,
    Transcriber,
    TranscriptionResult,
    load_transcriber_model,
)
from voicejournal.app.model_sources import DEFAULT_SPEECH_RECOGNITION_MODEL_KEY, model_source


class _FakeWhisperModel:
    def __init__(self, segments, info) -> None:
        self._segments = segments
        self._info = info
        self.calls: list[dict[str, object]] = []

    def transcribe(self, audio, **kwargs):
        call: dict[str, object] = {"kwargs": kwargs}
        if isinstance(audio, np.ndarray):
            call["audio_type"] = "ndarray"
            call["payload"] = np.array(audio, copy=True)
        else:
            call["audio_type"] = type(audio).__name__
            call["position"] = audio.tell()
            call["payload"] = audio.read()
            audio.seek(0)
        self.calls.append(call)
        return self._segments, self._info


def test_transcriber_uses_file_like_wav_buffers_and_explicit_options() -> None:
    iterated: list[str] = []

    def segment_iter():
        iterated.append("first")
        yield SimpleNamespace(text=" Hello")
        iterated.append("second")
        yield SimpleNamespace(text="   world\n")

    info = SimpleNamespace(
        language="en",
        language_probability=0.98,
        duration=1.25,
        duration_after_vad=1.25,
    )
    model = _FakeWhisperModel(segment_iter(), info)
    transcriber = Transcriber(model)
    audio = io.BytesIO(b"RIFF....WAVE")
    audio.seek(4)

    result = transcriber.transcribe(audio)

    assert iterated == ["first", "second"]
    assert model.calls == [
        {
            "audio_type": "BytesIO",
            "position": 0,
            "payload": b"RIFF....WAVE",
            "kwargs": {
                "language": TRANSCRIBE_LANGUAGE,
                "task": TRANSCRIBE_TASK,
                "beam_size": TRANSCRIBE_BEAM_SIZE,
                "condition_on_previous_text": False,
                "vad_filter": False,
                "word_timestamps": False,
            },
        }
    ]
    assert result == TranscriptionResult(
        text="Hello world",
        language="en",
        language_probability=0.98,
        duration=1.25,
        duration_after_vad=1.25,
        segment_count=2,
    )


def test_transcriber_converts_float_numpy_audio_to_float32() -> None:
    info = SimpleNamespace(language="en")
    model = _FakeWhisperModel(iter([SimpleNamespace(text=" hi")]), info)
    transcriber = Transcriber(model)

    result = transcriber.transcribe(np.array([0.1, -0.2, 0.3], dtype=np.float64))

    assert result.text == "hi"
    assert model.calls[0]["audio_type"] == "ndarray"
    assert np.array_equal(
        model.calls[0]["payload"],
        np.array([0.1, -0.2, 0.3], dtype=np.float32),
    )


def test_transcriber_rejects_pcm_integer_numpy_audio() -> None:
    info = SimpleNamespace(language="en")
    model = _FakeWhisperModel(iter([SimpleNamespace(text=" hi")]), info)
    transcriber = Transcriber(model)

    with pytest.raises(ValueError, match="normalized float waveform"):
        transcriber.transcribe(np.array([1, -2, 3], dtype=np.int16))

    assert model.calls == []


@pytest.mark.parametrize(
    "audio",
    [
        pytest.param(np.array([0.1, np.nan], dtype=np.float32), id="nan"),
        pytest.param(np.array([0.1, 2.0], dtype=np.float32), id="out-of-range"),
    ],
)
def test_transcriber_rejects_invalid_float_numpy_audio(audio: np.ndarray) -> None:
    info = SimpleNamespace(language="en")
    model = _FakeWhisperModel(iter([SimpleNamespace(text=" hi")]), info)
    transcriber = Transcriber(model)

    with pytest.raises(ValueError, match=r"finite float samples|normalized to the \[-1.0, 1.0\] range"):
        transcriber.transcribe(audio)

    assert model.calls == []


def test_transcriber_rejects_non_1d_numpy_audio() -> None:
    info = SimpleNamespace(language="en")
    model = _FakeWhisperModel(iter([SimpleNamespace(text=" hi")]), info)
    transcriber = Transcriber(model)

    with pytest.raises(ValueError, match="one-dimensional waveform"):
        transcriber.transcribe(np.zeros((2, 3), dtype=np.float32))

    assert model.calls == []


def test_load_transcriber_model_uses_local_model_directory(tmp_path) -> None:
    source = model_source(DEFAULT_SPEECH_RECOGNITION_MODEL_KEY)
    for artifact in source.artifacts:
        (tmp_path / artifact.filename).write_text("present")

    captured: dict[str, object] = {}

    def model_factory(model_path: str, *, device: str, compute_type: str):
        captured["model_path"] = model_path
        captured["device"] = device
        captured["compute_type"] = compute_type
        return _FakeWhisperModel(iter([]), SimpleNamespace())

    transcriber = load_transcriber_model(source, tmp_path, model_factory=model_factory)

    assert isinstance(transcriber, Transcriber)
    assert captured == {
        "model_path": str(tmp_path),
        "device": "cpu",
        "compute_type": "int8",
    }


def test_load_transcriber_model_requires_expected_artifacts(tmp_path) -> None:
    source = model_source(DEFAULT_SPEECH_RECOGNITION_MODEL_KEY)

    with pytest.raises(FileNotFoundError, match="missing"):
        load_transcriber_model(source, tmp_path, model_factory=lambda *_args, **_kwargs: object())