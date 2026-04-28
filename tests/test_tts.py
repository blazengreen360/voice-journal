from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from voicejournal.app.core.tts import (
    DEFAULT_VOICE_ID,
    MAX_SPEECH_RATE,
    MIN_SPEECH_RATE,
    TTSEngine,
    VOICE_BLEND_MAP,
    VOICE_MAP,
    load_tts_engine,
)
from voicejournal.app.model_sources import VOICE_REPLY_MODEL_KEY, model_source


class _FakeKokoro:
    def __init__(self, model_path: str, voices_path: str, *, available_voices: list[str] | None = None) -> None:
        self.model_path = model_path
        self.voices_path = voices_path
        self.available_voices = available_voices or [
            "af_sarah",
            "af_nicole",
            "af_heart",
            "am_echo",
            "am_liam",
            "am_eric",
        ]
        self.styles = {
            voice: np.full((2, 1, 3), index + 1, dtype=np.float32)
            for index, voice in enumerate(self.available_voices)
        }
        self.calls: list[dict[str, object]] = []

    def get_voices(self) -> list[str]:
        return list(self.available_voices)

    def get_voice_style(self, name: str) -> np.ndarray:
        return np.array(self.styles[name], copy=True)

    def create(self, text: str, *, voice: str | np.ndarray, speed: float, lang: str):
        self.calls.append(
            {
                "text": text,
                "voice": np.array(voice, copy=True) if isinstance(voice, np.ndarray) else voice,
                "speed": speed,
                "lang": lang,
            }
        )
        return np.array([0.1, -0.2, 0.3], dtype=np.float64), 24_000


def _expected_blend(fake: _FakeKokoro, voice_name: str) -> np.ndarray:
    blend = VOICE_BLEND_MAP[voice_name]
    total_weight = sum(weight for _voice_id, weight in blend)
    merged = sum(
        fake.get_voice_style(voice_id) * np.float32(weight / total_weight)
        for voice_id, weight in blend
    )
    return np.ascontiguousarray(merged, dtype=np.float32)


@pytest.mark.parametrize("voice_name", ["Lucy", "Allen"])
def test_tts_engine_synthesize_uses_blended_voice_style_for_preset(voice_name: str) -> None:
    fake = _FakeKokoro("model.onnx", "voices.bin")
    engine = TTSEngine("model.onnx", "voices.bin", kokoro_factory=lambda *_args: fake)

    samples, sample_rate = engine.synthesize("hello", voice_name=voice_name, speed=1.25)

    assert fake.calls[0]["text"] == "hello"
    assert fake.calls[0]["speed"] == 1.25
    assert fake.calls[0]["lang"] == "en-us"
    assert np.allclose(fake.calls[0]["voice"], _expected_blend(fake, voice_name))
    assert np.array_equal(samples, np.array([0.1, -0.2, 0.3], dtype=np.float32))
    assert sample_rate == 24_000


def test_tts_engine_clamps_speed_to_kokoro_supported_range() -> None:
    fake = _FakeKokoro("model.onnx", "voices.bin")
    engine = TTSEngine("model.onnx", "voices.bin", kokoro_factory=lambda *_args: fake)

    engine.synthesize("slow", voice_name="Lucy", speed=0.1)
    engine.synthesize("fast", voice_name="Lucy", speed=9.9)

    assert fake.calls[0]["speed"] == MIN_SPEECH_RATE
    assert fake.calls[1]["speed"] == MAX_SPEECH_RATE


def test_tts_engine_warns_when_configured_bundle_voices_are_missing(caplog) -> None:
    fake = _FakeKokoro("model.onnx", "voices.bin", available_voices=["bf_emma"])

    with caplog.at_level("WARNING"):
        TTSEngine("model.onnx", "voices.bin", kokoro_factory=lambda *_args: fake)

    assert "Configured Kokoro voices missing from bundle: af_sarah, am_echo" in caplog.text


@pytest.mark.parametrize(
    ("voice_name", "available_voices", "expected_base_voice", "expected_warning"),
    [
        (
            "Lucy",
            ["af_sarah", "am_echo"],
            "af_sarah",
            "Voice preset Lucy missing blend components: af_nicole, af_heart; using af_sarah",
        ),
        (
            "Allen",
            ["af_sarah", "am_echo"],
            "am_echo",
            "Voice preset Allen missing blend components: am_liam, am_eric; using am_echo",
        ),
    ],
)
def test_tts_engine_uses_base_voice_when_blend_components_are_missing(
    caplog, voice_name: str, available_voices: list[str], expected_base_voice: str, expected_warning: str
) -> None:
    fake = _FakeKokoro("model.onnx", "voices.bin", available_voices=available_voices)

    with caplog.at_level("WARNING"):
        engine = TTSEngine("model.onnx", "voices.bin", kokoro_factory=lambda *_args: fake)
        engine.synthesize("hello", voice_name=voice_name, speed=1.0)

    assert fake.calls[0]["voice"] == expected_base_voice
    assert expected_warning in caplog.text


def test_tts_engine_falls_back_to_first_available_voice_and_logs_warning(caplog) -> None:
    fake = _FakeKokoro("model.onnx", "voices.bin", available_voices=["bf_emma", "zz_voice"])
    engine = TTSEngine("model.onnx", "voices.bin", kokoro_factory=lambda *_args: fake)

    with caplog.at_level("WARNING"):
        engine.synthesize("hello", voice_name="Lucy", speed=1.0)

    assert fake.calls[0]["voice"] == "bf_emma"
    assert "falling back to bf_emma" in caplog.text


def test_load_tts_engine_uses_expected_model_and_voice_artifacts(tmp_path) -> None:
    source = model_source(VOICE_REPLY_MODEL_KEY)
    for artifact in source.artifacts:
        (tmp_path / artifact.filename).write_text("present")

    captured: dict[str, object] = {}

    def kokoro_factory(model_path: str, voices_path: str):
        captured["model_path"] = model_path
        captured["voices_path"] = voices_path
        return _FakeKokoro(model_path, voices_path)

    engine = load_tts_engine(source, tmp_path, kokoro_factory=kokoro_factory)

    assert isinstance(engine, TTSEngine)
    assert captured == {
        "model_path": str(tmp_path / "kokoro-v1.0.int8.onnx"),
        "voices_path": str(tmp_path / "voices-v1.0.bin"),
    }


def test_load_tts_engine_requires_all_artifacts(tmp_path) -> None:
    source = model_source(VOICE_REPLY_MODEL_KEY)

    with pytest.raises(FileNotFoundError, match="missing"):
        load_tts_engine(source, tmp_path, kokoro_factory=lambda *_args: _FakeKokoro("m", "v"))