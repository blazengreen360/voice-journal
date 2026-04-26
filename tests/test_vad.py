import hashlib

from voicejournal.app.bundled_assets import SILERO_VAD_SHA256, bundled_silero_vad_model


def test_bundled_silero_model_is_present_and_pinned() -> None:
    model = bundled_silero_vad_model()

    assert model.is_file()
    assert hashlib.sha256(model.read_bytes()).hexdigest() == SILERO_VAD_SHA256
