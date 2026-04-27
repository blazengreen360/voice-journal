from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time

import pytest
from PySide6.QtCore import QCoreApplication

from voicejournal.app import model_downloader as model_downloader_module
from voicejournal.app.model_downloader import ModelDownloader
from voicejournal.app.model_sources import (
    DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
    DEFAULT_WRITING_HELP_MODEL_KEY,
    FALLBACK_WRITING_HELP_MODEL_KEY,
    MODEL_SOURCES,
    VOICE_REPLY_MODEL_KEY,
    WRITING_HELP_PLUS_MODEL_KEY,
    ModelArtifact,
    ModelSource,
)


@dataclass(slots=True)
class FakeResource:
    data: bytes
    ignore_range: bool = False
    chunk_size: int | None = None
    delay_seconds: float = 0.0


class FakeResponse:
    def __init__(
        self,
        data: bytes,
        *,
        status: int,
        chunk_size: int | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.status = status
        self.headers = {"Content-Length": str(len(data))}
        self._chunk_size = chunk_size
        self._delay_seconds = delay_seconds
        self._data = data
        self._offset = 0

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if self._chunk_size is not None:
            size = self._chunk_size if size < 0 else min(size, self._chunk_size)
        elif size < 0:
            size = len(self._data) - self._offset
        end = min(self._offset + size, len(self._data))
        chunk = self._data[self._offset:end]
        self._offset = end
        if self._delay_seconds > 0:
            time.sleep(self._delay_seconds)
        return chunk


class FakeUrlOpen:
    def __init__(self, resources: dict[str, FakeResource]) -> None:
        self._resources = resources
        self.requests: list[tuple[str, str | None]] = []

    def __call__(self, request, timeout: int = 30) -> FakeResponse:
        assert timeout == 30
        url = request.full_url
        headers = {name.lower(): value for name, value in request.header_items()}
        range_header = headers.get("range")
        self.requests.append((url, range_header))

        resource = self._resources[url]
        data = resource.data
        status = 200
        if range_header is not None and not resource.ignore_range:
            prefix, suffix = range_header.split("=")
            assert prefix == "bytes"
            start_text, _, end_text = suffix.partition("-")
            assert end_text == ""
            data = data[int(start_text) :]
            status = 206
        return FakeResponse(
            data,
            status=status,
            chunk_size=resource.chunk_size,
            delay_seconds=resource.delay_seconds,
        )


@pytest.fixture()
def qapp() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_for(url: str, filename: str, data: bytes) -> ModelSource:
    return ModelSource.single_file(
        key=filename,
        display=filename,
        filename=filename,
        url=url,
        sha256=_sha256_bytes(data),
        size_bytes=len(data),
    )


def _bundle_source(key: str, install_dir: str, files: list[tuple[str, str, bytes]]) -> ModelSource:
    return ModelSource(
        key=key,
        display=key,
        install_dir=install_dir,
        artifacts=tuple(
            ModelArtifact(
                filename=filename,
                url=url,
                sha256=_sha256_bytes(data),
                size_bytes=len(data),
            )
            for filename, url, data in files
        ),
    )


def _wait_until(predicate, qapp: QCoreApplication, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


def _is_idle(downloader: ModelDownloader) -> bool:
    thread = downloader._active_thread
    return thread is None or not thread.is_alive()


def test_model_sources_define_required_downloads() -> None:
    speech = MODEL_SOURCES[DEFAULT_SPEECH_RECOGNITION_MODEL_KEY]
    assert speech.install_dir == "faster-whisper-base.en"
    assert speech.filenames == (
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    )
    assert speech.artifacts[1].url == (
        "https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/model.bin"
    )
    assert speech.size_bytes == 150_518_441
    assert MODEL_SOURCES[DEFAULT_WRITING_HELP_MODEL_KEY].filename == "gemma-4-E2B-it-Q8_0.gguf"
    assert MODEL_SOURCES[DEFAULT_WRITING_HELP_MODEL_KEY].url == (
        "https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q8_0.gguf"
    )
    assert MODEL_SOURCES[DEFAULT_WRITING_HELP_MODEL_KEY].size_bytes == 4_967_494_592
    assert MODEL_SOURCES[WRITING_HELP_PLUS_MODEL_KEY].filename == "gemma-4-E4B-it-Q4_K_M.gguf"
    assert MODEL_SOURCES[WRITING_HELP_PLUS_MODEL_KEY].url == (
        "https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
    )
    assert MODEL_SOURCES[WRITING_HELP_PLUS_MODEL_KEY].size_bytes == 5_335_289_824
    assert MODEL_SOURCES[FALLBACK_WRITING_HELP_MODEL_KEY].filename == "gemma-3-1b-it-Q4_K_M.gguf"
    assert MODEL_SOURCES[FALLBACK_WRITING_HELP_MODEL_KEY].url == (
        "https://huggingface.co/ggml-org/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf"
    )
    assert MODEL_SOURCES[FALLBACK_WRITING_HELP_MODEL_KEY].size_bytes == 806_000_000
    voice = MODEL_SOURCES[VOICE_REPLY_MODEL_KEY]
    assert voice.install_dir == "kokoro-v1.0"
    assert voice.filenames == ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")
    assert voice.artifacts[0].url == (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
    )
    assert voice.artifacts[1].url == (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
    )
    assert voice.size_bytes == 120_575_669


def test_model_downloader_skips_network_for_valid_installed_file(tmp_path, monkeypatch, qapp) -> None:
    payload = b"installed"
    source = _source_for("https://example.test/installed.bin", "installed.bin", payload)
    final_path = tmp_path / source.filename
    final_path.write_bytes(payload)

    downloader = ModelDownloader(tmp_path)
    finished: list[str] = []
    failed: list[tuple[str, str]] = []
    downloader.finished.connect(lambda key: finished.append(key))
    downloader.failed.connect(lambda key, message: failed.append((key, message)))
    monkeypatch.setattr(
        model_downloader_module.urllib_request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used")),
    )

    downloader.enqueue(source)

    _wait_until(lambda: finished == [source.key] and _is_idle(downloader), qapp)
    assert failed == []


def test_model_downloader_promotes_valid_partial_without_network(tmp_path, monkeypatch, qapp) -> None:
    payload = b"valid-partial"
    source = _source_for("https://example.test/partial.bin", "partial.bin", payload)
    partial_path = tmp_path / f".{source.filename}.partial"
    partial_path.write_bytes(payload)

    downloader = ModelDownloader(tmp_path)
    finished: list[str] = []
    downloader.finished.connect(lambda key: finished.append(key))
    monkeypatch.setattr(
        model_downloader_module.urllib_request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used")),
    )

    downloader.enqueue(source)

    _wait_until(lambda: finished == [source.key] and _is_idle(downloader), qapp)
    assert (tmp_path / source.filename).read_bytes() == payload
    assert partial_path.exists() is False


def test_model_downloader_resumes_existing_partial_file(tmp_path, monkeypatch, qapp) -> None:
    payload = b"resume-me-please"
    source = _source_for("https://example.test/resume.bin", "resume.bin", payload)
    partial_path = tmp_path / f".{source.filename}.partial"
    partial_path.write_bytes(payload[:7])
    fake_urlopen = FakeUrlOpen({source.url: FakeResource(payload)})
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    finished: list[str] = []
    downloader.finished.connect(lambda key: finished.append(key))

    downloader.enqueue(source)

    _wait_until(lambda: finished == [source.key] and _is_idle(downloader), qapp)
    assert fake_urlopen.requests == [(source.url, "bytes=7-")]
    assert (tmp_path / source.filename).read_bytes() == payload
    assert partial_path.exists() is False


def test_model_downloader_discards_corrupt_full_size_partial_before_retry(tmp_path, monkeypatch, qapp) -> None:
    payload = b"fresh-retry"
    source = _source_for("https://example.test/full-partial.bin", "full-partial.bin", payload)
    partial_path = tmp_path / f".{source.filename}.partial"
    partial_path.write_bytes(b"stale-bytes")
    fake_urlopen = FakeUrlOpen({source.url: FakeResource(payload)})
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    finished: list[str] = []
    downloader.finished.connect(lambda key: finished.append(key))

    downloader.enqueue(source)

    _wait_until(lambda: finished == [source.key] and _is_idle(downloader), qapp)
    assert fake_urlopen.requests == [(source.url, None)]
    assert (tmp_path / source.filename).read_bytes() == payload
    assert partial_path.exists() is False


def test_model_downloader_restarts_when_server_ignores_range(tmp_path, monkeypatch, qapp) -> None:
    payload = b"clean-restart"
    source = _source_for("https://example.test/restart.bin", "restart.bin", payload)
    partial_path = tmp_path / f".{source.filename}.partial"
    partial_path.write_bytes(b"garbage")
    fake_urlopen = FakeUrlOpen({source.url: FakeResource(payload, ignore_range=True)})
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    finished: list[str] = []
    downloader.finished.connect(lambda key: finished.append(key))

    downloader.enqueue(source)

    _wait_until(lambda: finished == [source.key] and _is_idle(downloader), qapp)
    assert fake_urlopen.requests == [(source.url, "bytes=7-")]
    assert (tmp_path / source.filename).read_bytes() == payload
    assert partial_path.exists() is False


def test_model_downloader_downloads_bundle_into_install_directory(tmp_path, monkeypatch, qapp) -> None:
    source = _bundle_source(
        "bundle-model",
        "bundle-model",
        [
            ("config.json", "https://example.test/bundle/config.json", b"{}"),
            ("model.bin", "https://example.test/bundle/model.bin", b"weights"),
        ],
    )
    fake_urlopen = FakeUrlOpen(
        {
            source.artifacts[0].url: FakeResource(b"{}"),
            source.artifacts[1].url: FakeResource(b"weights"),
        }
    )
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    finished: list[str] = []
    downloader.finished.connect(lambda key: finished.append(key))

    downloader.enqueue(source)

    _wait_until(lambda: finished == [source.key] and _is_idle(downloader), qapp)
    assert (tmp_path / source.install_dir / "config.json").read_bytes() == b"{}"
    assert (tmp_path / source.install_dir / "model.bin").read_bytes() == b"weights"
    assert fake_urlopen.requests == [
        (source.artifacts[0].url, None),
        (source.artifacts[1].url, None),
    ]


def test_model_downloader_removes_partial_on_hash_mismatch(tmp_path, monkeypatch, qapp) -> None:
    source = _source_for("https://example.test/bad.bin", "bad.bin", b"expected")
    fake_urlopen = FakeUrlOpen({source.url: FakeResource(b"unexpected")})
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    failed: list[tuple[str, str]] = []
    downloader.failed.connect(lambda key, message: failed.append((key, message)))

    downloader.enqueue(source)

    _wait_until(lambda: len(failed) == 1 and _is_idle(downloader), qapp)
    assert failed[0][0] == source.key
    assert failed[0][1].startswith("Hash mismatch")
    assert (tmp_path / source.filename).exists() is False
    assert (tmp_path / f".{source.filename}.partial").exists() is False


def test_model_downloader_cancel_preserves_partial_and_resume_completes(tmp_path, monkeypatch, qapp) -> None:
    payload = (b"0123456789abcdef" * 32)
    source = _source_for("https://example.test/cancel.bin", "cancel.bin", payload)
    fake_urlopen = FakeUrlOpen(
        {source.url: FakeResource(payload, chunk_size=32, delay_seconds=0.02)}
    )
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    progress: list[tuple[str, int, int]] = []
    finished: list[str] = []
    failed: list[tuple[str, str]] = []
    downloader.progress.connect(lambda key, done, total: progress.append((key, done, total)))
    downloader.finished.connect(lambda key: finished.append(key))
    downloader.failed.connect(lambda key, message: failed.append((key, message)))

    downloader.enqueue(source)

    partial_path = tmp_path / f".{source.filename}.partial"
    _wait_until(lambda: bool(progress) and partial_path.exists(), qapp)
    downloader.cancel()
    _wait_until(lambda: len(failed) == 1 and _is_idle(downloader), qapp)

    partial_size = partial_path.stat().st_size
    assert 0 < partial_size < len(payload)
    assert failed[0] == (source.key, "Cancelled")
    assert (tmp_path / source.filename).exists() is False

    downloader.enqueue(source)

    _wait_until(lambda: finished == [source.key] and _is_idle(downloader), qapp)
    assert fake_urlopen.requests[1] == (source.url, f"bytes={partial_size}-")
    assert (tmp_path / source.filename).read_bytes() == payload
    assert partial_path.exists() is False


def test_model_downloader_cancel_only_stops_active_download_when_queue_exists(tmp_path, monkeypatch, qapp) -> None:
    first_payload = b"a" * 512
    second_payload = b"done"
    first = _source_for("https://example.test/queued-first.bin", "queued-first.bin", first_payload)
    second = _source_for("https://example.test/queued-second.bin", "queued-second.bin", second_payload)
    fake_urlopen = FakeUrlOpen(
        {
            first.url: FakeResource(first_payload, chunk_size=32, delay_seconds=0.02),
            second.url: FakeResource(second_payload),
        }
    )
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    progress: list[tuple[str, int, int]] = []
    finished: list[str] = []
    failed: list[tuple[str, str]] = []
    downloader.progress.connect(lambda key, done, total: progress.append((key, done, total)))
    downloader.finished.connect(lambda key: finished.append(key))
    downloader.failed.connect(lambda key, message: failed.append((key, message)))

    downloader.enqueue(first)
    downloader.enqueue(second)

    first_partial = tmp_path / f".{first.filename}.partial"
    _wait_until(lambda: any(key == first.key for key, _, _ in progress) and first_partial.exists(), qapp)
    downloader.cancel()

    _wait_until(
        lambda: failed == [(first.key, "Cancelled")] and finished == [second.key] and _is_idle(downloader),
        qapp,
    )
    assert first_partial.exists()
    assert (tmp_path / second.filename).read_bytes() == second_payload
    assert fake_urlopen.requests == [(first.url, None), (second.url, None)]


def test_model_downloader_serializes_queued_downloads(tmp_path, monkeypatch, qapp) -> None:
    first_payload = (b"first-download" * 32)
    second_payload = b"second-download"
    first = _source_for("https://example.test/first.bin", "first.bin", first_payload)
    second = _source_for("https://example.test/second.bin", "second.bin", second_payload)
    fake_urlopen = FakeUrlOpen(
        {
            first.url: FakeResource(first_payload, chunk_size=32, delay_seconds=0.02),
            second.url: FakeResource(second_payload),
        }
    )
    monkeypatch.setattr(model_downloader_module.urllib_request, "urlopen", fake_urlopen)

    downloader = ModelDownloader(tmp_path)
    progress: list[tuple[str, int, int]] = []
    finished: list[str] = []
    downloader.progress.connect(lambda key, done, total: progress.append((key, done, total)))
    downloader.finished.connect(lambda key: finished.append(key))

    downloader.enqueue(first)
    downloader.enqueue(second)

    _wait_until(lambda: any(key == first.key for key, _, _ in progress), qapp)
    assert fake_urlopen.requests == [(first.url, None)]

    _wait_until(lambda: finished == [first.key, second.key] and _is_idle(downloader), qapp)
    assert fake_urlopen.requests == [(first.url, None), (second.url, None)]
    assert (tmp_path / first.filename).read_bytes() == first_payload
    assert (tmp_path / second.filename).read_bytes() == second_payload