from __future__ import annotations

from importlib.abc import Traversable

from voicejournal.app.config import AppConfig


SILERO_VAD_SOURCE_TAG = "v6.2.1"
SILERO_VAD_SOURCE_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    f"{SILERO_VAD_SOURCE_TAG}/src/silero_vad/data/silero_vad.onnx"
)
SILERO_VAD_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


def bundled_silero_vad_model() -> Traversable:
    return AppConfig().assets_dir().joinpath("models").joinpath("silero_vad.onnx")
