from __future__ import annotations

from dataclasses import dataclass


UNPINNED_SHA256 = "<TBD-FILL-AT-RELEASE>"

DEFAULT_SPEECH_RECOGNITION_MODEL_KEY = "whisper-base.en"
DEFAULT_WRITING_HELP_MODEL_KEY = "gemma-4-E2B-it-Q8_0"
WRITING_HELP_PLUS_MODEL_KEY = "gemma-4-E4B-it-Q4_K_M"
FALLBACK_WRITING_HELP_MODEL_KEY = "gemma-3-1b-it-Q4_K_M"
VOICE_REPLY_MODEL_KEY = "kokoro-v1.0"


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    filename: str
    url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ModelSource:
    key: str
    display: str
    artifacts: tuple[ModelArtifact, ...]
    install_dir: str | None = None

    def __post_init__(self) -> None:
        if not self.artifacts:
            raise ValueError("ModelSource requires at least one artifact")
        if len(self.artifacts) > 1 and self.install_dir is None:
            raise ValueError("Bundle sources must define an install_dir")

    @classmethod
    def single_file(
        cls,
        *,
        key: str,
        display: str,
        filename: str,
        url: str,
        sha256: str,
        size_bytes: int,
    ) -> ModelSource:
        return cls(
            key=key,
            display=display,
            artifacts=(
                ModelArtifact(
                    filename=filename,
                    url=url,
                    sha256=sha256,
                    size_bytes=size_bytes,
                ),
            ),
        )

    @property
    def size_bytes(self) -> int:
        return sum(artifact.size_bytes for artifact in self.artifacts)

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(artifact.filename for artifact in self.artifacts)

    @property
    def filename(self) -> str:
        if len(self.artifacts) != 1:
            raise AttributeError("Bundle sources do not expose a single filename")
        return self.artifacts[0].filename

    @property
    def url(self) -> str:
        if len(self.artifacts) != 1:
            raise AttributeError("Bundle sources do not expose a single url")
        return self.artifacts[0].url

    @property
    def sha256(self) -> str:
        if len(self.artifacts) != 1:
            raise AttributeError("Bundle sources do not expose a single sha256")
        return self.artifacts[0].sha256


MODEL_SOURCES: dict[str, ModelSource] = {
    DEFAULT_SPEECH_RECOGNITION_MODEL_KEY: ModelSource(
        key=DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
        display="Speech recognition",
        install_dir="faster-whisper-base.en",
        artifacts=(
            ModelArtifact(
                filename="config.json",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/config.json",
                sha256="f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb",
                size_bytes=2_227,
            ),
            ModelArtifact(
                filename="model.bin",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/model.bin",
                sha256="2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
                size_bytes=145_216_508,
            ),
            ModelArtifact(
                filename="tokenizer.json",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/tokenizer.json",
                sha256="929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
                size_bytes=2_128_466,
            ),
            ModelArtifact(
                filename="vocabulary.txt",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/vocabulary.txt",
                sha256="ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
                size_bytes=422_309,
            ),
        ),
    ),
    DEFAULT_WRITING_HELP_MODEL_KEY: ModelSource.single_file(
        key=DEFAULT_WRITING_HELP_MODEL_KEY,
        display="Writing help",
        filename="gemma-4-E2B-it-Q8_0.gguf",
        url="https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q8_0.gguf",
        sha256="e049411c01fb7a81161768c52e38828970e55a64e22738957adcbe51d20f1c8e",
        size_bytes=4_967_494_592,
    ),
    WRITING_HELP_PLUS_MODEL_KEY: ModelSource.single_file(
        key=WRITING_HELP_PLUS_MODEL_KEY,
        display="Writing help Plus",
        filename="gemma-4-E4B-it-Q4_K_M.gguf",
        url="https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf",
        sha256="90ce98129eb3e8cc57e62433d500c97c624b1e3af1fcc85dd3b55ad7e0313e9f",
        size_bytes=5_335_289_824,
    ),
    FALLBACK_WRITING_HELP_MODEL_KEY: ModelSource.single_file(
        key=FALLBACK_WRITING_HELP_MODEL_KEY,
        display="Writing help fallback",
        filename="gemma-3-1b-it-Q4_K_M.gguf",
        url="https://huggingface.co/ggml-org/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
        sha256="8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135",
        size_bytes=806_058_240,
    ),
    VOICE_REPLY_MODEL_KEY: ModelSource(
        key=VOICE_REPLY_MODEL_KEY,
        display="Voice replies",
        install_dir="kokoro-v1.0",
        artifacts=(
            ModelArtifact(
                filename="kokoro-v1.0.int8.onnx",
                url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
                sha256="6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
                size_bytes=92_361_271,
            ),
            ModelArtifact(
                filename="voices-v1.0.bin",
                url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
                sha256="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
                size_bytes=28_214_398,
            ),
        ),
    ),
}


def model_source(key: str) -> ModelSource:
    return MODEL_SOURCES[key]