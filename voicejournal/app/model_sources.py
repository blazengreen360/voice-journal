from __future__ import annotations

from dataclasses import dataclass


UNPINNED_SHA256 = "<TBD-FILL-AT-RELEASE>"

DEFAULT_SPEECH_RECOGNITION_MODEL_KEY = "whisper-base.en"
DEFAULT_WRITING_HELP_MODEL_KEY = "gemma-4-E2B-it-Q8_0"
WRITING_HELP_PLUS_MODEL_KEY = "gemma-4-E4B-it-Q4_K_M"
FALLBACK_WRITING_HELP_MODEL_KEY = "gemma-3-1B-it-Q4_K_M"
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
        display="Whisper base.en",
        install_dir="faster-whisper-base.en",
        artifacts=(
            ModelArtifact(
                filename="config.json",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/config.json",
                sha256=UNPINNED_SHA256,
                size_bytes=2_230,
            ),
            ModelArtifact(
                filename="model.bin",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/model.bin",
                sha256=UNPINNED_SHA256,
                size_bytes=147_964_211,
            ),
            ModelArtifact(
                filename="tokenizer.json",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/tokenizer.json",
                sha256=UNPINNED_SHA256,
                size_bytes=2_130_000,
            ),
            ModelArtifact(
                filename="vocabulary.txt",
                url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/vocabulary.txt",
                sha256=UNPINNED_SHA256,
                size_bytes=422_000,
            ),
        ),
    ),
    DEFAULT_WRITING_HELP_MODEL_KEY: ModelSource.single_file(
        key=DEFAULT_WRITING_HELP_MODEL_KEY,
        display="Gemma 4 E2B IT (Q8_0)",
        filename="gemma-4-E2B-it-Q8_0.gguf",
        url="https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q8_0.gguf",
        sha256=UNPINNED_SHA256,
        size_bytes=4_967_494_592,
    ),
    WRITING_HELP_PLUS_MODEL_KEY: ModelSource.single_file(
        key=WRITING_HELP_PLUS_MODEL_KEY,
        display="Gemma 4 E4B IT (Q4_K_M)",
        filename="gemma-4-E4B-it-Q4_K_M.gguf",
        url="https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf",
        sha256=UNPINNED_SHA256,
        size_bytes=5_335_289_824,
    ),
    FALLBACK_WRITING_HELP_MODEL_KEY: ModelSource.single_file(
        key=FALLBACK_WRITING_HELP_MODEL_KEY,
        display="Gemma 3 1B IT (Q4_K_M)",
        filename="gemma-3-1b-it-Q4_K_M.gguf",
        url="https://huggingface.co/ggml-org/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
        sha256=UNPINNED_SHA256,
        size_bytes=806_000_000,
    ),
    VOICE_REPLY_MODEL_KEY: ModelSource(
        key=VOICE_REPLY_MODEL_KEY,
        display="Voice replies",
        install_dir="kokoro-v1.0",
        artifacts=(
            ModelArtifact(
                filename="kokoro-v1.0.int8.onnx",
                url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
                sha256=UNPINNED_SHA256,
                size_bytes=92_361_271,
            ),
            ModelArtifact(
                filename="voices-v1.0.bin",
                url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
                sha256=UNPINNED_SHA256,
                size_bytes=28_214_398,
            ),
        ),
    ),
}


def model_source(key: str) -> ModelSource:
    return MODEL_SOURCES[key]