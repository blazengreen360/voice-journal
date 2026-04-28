from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path

import numpy as np

from voicejournal.app.model_sources import ModelSource


log = logging.getLogger(__name__)

VOICE_MAP = {
    "Lucy": "af_sarah",
    "Allen": "am_echo",
}
VOICE_BLEND_MAP = {
    "Lucy": (("af_sarah", 0.6), ("af_nicole", 0.25), ("af_heart", 0.15)),
    "Allen": (("am_echo", 0.55), ("am_liam", 0.3), ("am_eric", 0.15)),
}
DEFAULT_VOICE_ID = "af_sarah"
MIN_SPEECH_RATE = 0.5
MAX_SPEECH_RATE = 2.0

VoiceStyle = str | np.ndarray


class TTSEngine:
    def __init__(
        self,
        model_path: str | Path,
        voices_path: str | Path,
        *,
        kokoro_factory: Callable[..., object] | None = None,
    ) -> None:
        if kokoro_factory is None:
            from kokoro_onnx import Kokoro

            kokoro_factory = Kokoro

        self._kokoro = kokoro_factory(str(model_path), str(voices_path))
        self._available = tuple(self._kokoro.get_voices())
        if not self._available:
            raise RuntimeError("No Kokoro voices are available.")

        configured_voice_ids = {DEFAULT_VOICE_ID, *VOICE_MAP.values()}
        missing_configured_voices = sorted(configured_voice_ids - set(self._available))
        if missing_configured_voices:
            log.warning(
                "Configured Kokoro voices missing from bundle: %s",
                ", ".join(missing_configured_voices),
            )
        self._voice_styles = self._build_voice_styles()

    def synthesize(
        self,
        text: str,
        voice_name: str,
        speed: float = 1.0,
    ) -> tuple[np.ndarray, int]:
        voice_style = self._voice_styles.get(voice_name, VOICE_MAP.get(voice_name, DEFAULT_VOICE_ID))
        if isinstance(voice_style, str) and voice_style not in self._available:
            fallback_voice = self._available[0]
            log.warning("Voice %s missing; falling back to %s", voice_style, fallback_voice)
            voice_style = fallback_voice

        clamped_speed = max(MIN_SPEECH_RATE, min(MAX_SPEECH_RATE, speed))
        samples, sample_rate = self._kokoro.create(
            text,
            voice=voice_style,
            speed=clamped_speed,
            lang="en-us",
        )
        return np.asarray(samples, dtype=np.float32).reshape(-1), int(sample_rate)

    def _build_voice_styles(self) -> dict[str, VoiceStyle]:
        voice_styles: dict[str, VoiceStyle] = {}
        available_voices = set(self._available)
        for voice_name, base_voice in VOICE_MAP.items():
            blend = VOICE_BLEND_MAP.get(voice_name)
            if blend is None:
                if base_voice in available_voices:
                    voice_styles[voice_name] = base_voice
                continue

            missing_components = [voice_id for voice_id, _weight in blend if voice_id not in available_voices]
            if missing_components:
                log.warning(
                    "Voice preset %s missing blend components: %s; using %s",
                    voice_name,
                    ", ".join(missing_components),
                    base_voice,
                )
                if base_voice in available_voices:
                    voice_styles[voice_name] = base_voice
                continue

            voice_styles[voice_name] = self._blend_voice_style(blend)
        return voice_styles

    def _blend_voice_style(self, blend: tuple[tuple[str, float], ...]) -> np.ndarray:
        total_weight = sum(weight for _voice_id, weight in blend)
        if total_weight <= 0:
            raise ValueError("Voice blend weights must sum to a positive value.")

        blended: np.ndarray | None = None
        for voice_id, weight in blend:
            style = np.asarray(self._kokoro.get_voice_style(voice_id), dtype=np.float32)
            scaled_style = style * np.float32(weight / total_weight)
            blended = scaled_style if blended is None else blended + scaled_style
        assert blended is not None
        return np.ascontiguousarray(blended, dtype=np.float32)


def load_tts_engine(
    source: ModelSource,
    model_path: Path,
    *,
    kokoro_factory: Callable[..., object] | None = None,
) -> TTSEngine:
    missing = [artifact.filename for artifact in source.artifacts if not (model_path / artifact.filename).is_file()]
    if missing:
        missing_text = ", ".join(missing)
        raise FileNotFoundError(f"{source.display} is not installed; missing {missing_text}")

    onnx_path = next((model_path / artifact.filename for artifact in source.artifacts if artifact.filename.endswith(".onnx")), None)
    voices_path = next((model_path / artifact.filename for artifact in source.artifacts if "voices" in artifact.filename), None)
    if onnx_path is None or voices_path is None:
        raise FileNotFoundError(f"{source.display} install layout is invalid.")

    return TTSEngine(onnx_path, voices_path, kokoro_factory=kokoro_factory)


__all__ = [
    "DEFAULT_VOICE_ID",
    "MAX_SPEECH_RATE",
    "MIN_SPEECH_RATE",
    "VOICE_BLEND_MAP",
    "TTSEngine",
    "VOICE_MAP",
    "load_tts_engine",
]