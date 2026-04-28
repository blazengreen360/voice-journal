from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from voicejournal.app.config import AppPaths
from voicejournal.app.model_registry import ModelRegistry
from voicejournal.app.model_sources import (
    DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
    DEFAULT_WRITING_HELP_MODEL_KEY,
    FALLBACK_WRITING_HELP_MODEL_KEY,
    VOICE_REPLY_MODEL_KEY,
    WRITING_HELP_PLUS_MODEL_KEY,
    model_source,
)


@dataclass(frozen=True, slots=True)
class _FakeConfig:
    _paths: AppPaths

    def paths(self) -> AppPaths:
        return self._paths


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_config(tmp_path: Path) -> _FakeConfig:
    paths = AppPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        logs_dir=tmp_path / "data" / "logs",
        models_dir=tmp_path / "models",
        photos_dir=tmp_path / "photos",
    )
    for path in (
        paths.config_dir,
        paths.data_dir,
        paths.cache_dir,
        paths.logs_dir,
        paths.models_dir,
        paths.photos_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return _FakeConfig(paths)


def _wait_until(predicate, qapp: QApplication, pool: QThreadPool, timeout_ms: int = 5000) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            break
        pool.waitForDone(10)

    pool.waitForDone(timeout_ms)
    for _ in range(5):
        qapp.processEvents()

    assert predicate()


def test_model_registry_load_all_runs_loaders_in_parallel(tmp_path: Path, qapp: QApplication) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    pool.setMaxThreadCount(3)
    gate = threading.Event()
    lock = threading.Lock()
    started: list[str] = []
    ready: list[str] = []

    def make_loader(name: str):
        def load(_source, _path):
            with lock:
                started.append(name)
            gate.wait(1)
            return name

        return load

    registry = ModelRegistry(
        config,
        thread_pool=pool,
        whisper_loader=make_loader("whisper"),
        llm_loader=make_loader("llm"),
        tts_loader=make_loader("tts"),
    )
    registry.whisper_ready.connect(lambda: ready.append("whisper"))
    registry.llm_ready.connect(lambda: ready.append("llm"))
    registry.tts_ready.connect(lambda: ready.append("tts"))
    registry.all_ready.connect(lambda: ready.append("all"))

    registry.load_all()

    _wait_until(lambda: len(started) == 3, qapp, pool)
    gate.set()
    _wait_until(lambda: set(ready) == {"whisper", "llm", "tts", "all"}, qapp, pool)

    assert registry.transcriber == "whisper"
    assert registry.llm == "llm"
    assert registry.tts == "tts"
    assert registry.all_loaded is True
    assert ready.count("all") == 1


def test_model_registry_uses_default_whisper_loader_function(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    ready: list[str] = []
    whisper_calls: list[tuple[str, Path]] = []
    transcriber = object()

    def fake_whisper_loader(source, model_path):
        whisper_calls.append((source.key, model_path))
        return transcriber

    monkeypatch.setattr("voicejournal.app.model_registry.load_transcriber_model", fake_whisper_loader)

    registry = ModelRegistry(
        config,
        thread_pool=pool,
        llm_loader=lambda _source, _path: "llm",
        tts_loader=lambda _source, _path: "tts",
    )
    registry.whisper_ready.connect(lambda: ready.append("whisper"))

    registry.load_all()
    _wait_until(lambda: ready == ["whisper"], qapp, pool)

    assert registry.transcriber is transcriber
    assert whisper_calls == [
        (
            DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
            config.paths().models_dir / "faster-whisper-base.en",
        )
    ]


def test_model_registry_uses_default_llm_loader_function(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    ready: list[str] = []
    llm_calls: list[tuple[str, Path]] = []
    engine = object()

    def fake_llm_loader(source, model_path):
        llm_calls.append((source.key, model_path))
        return engine

    monkeypatch.setattr("voicejournal.app.model_registry.load_llm_engine", fake_llm_loader)

    registry = ModelRegistry(
        config,
        thread_pool=pool,
        whisper_loader=lambda _source, _path: "whisper",
        tts_loader=lambda _source, _path: "tts",
    )
    registry.llm_ready.connect(lambda: ready.append("llm"))

    registry.load_all()
    _wait_until(lambda: ready == ["llm"], qapp, pool)

    assert registry.llm is engine
    assert llm_calls == [
        (
            DEFAULT_WRITING_HELP_MODEL_KEY,
            config.paths().models_dir / "gemma-4-E2B-it-Q8_0.gguf",
        )
    ]


def test_model_registry_uses_default_tts_loader_function(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    ready: list[str] = []
    tts_calls: list[tuple[str, Path]] = []
    engine = object()

    def fake_tts_loader(source, model_path):
        tts_calls.append((source.key, model_path))
        return engine

    monkeypatch.setattr("voicejournal.app.model_registry.load_tts_engine", fake_tts_loader)

    registry = ModelRegistry(
        config,
        thread_pool=pool,
        whisper_loader=lambda _source, _path: "whisper",
        llm_loader=lambda _source, _path: "llm",
    )
    registry.tts_ready.connect(lambda: ready.append("tts"))

    registry.load_all()
    _wait_until(lambda: ready == ["tts"], qapp, pool)

    assert registry.tts is engine
    assert tts_calls == [
        (
            VOICE_REPLY_MODEL_KEY,
            config.paths().models_dir / "kokoro-v1.0",
        )
    ]


def test_model_registry_ensure_llm_loaded_uses_synchronous_fast_path(
    tmp_path: Path, qapp: QApplication
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    llm_calls: list[str] = []
    ready: list[str] = []

    def load_llm(source, _path):
        llm_calls.append(source.key)
        return f"loaded:{source.key}"

    registry = ModelRegistry(config, thread_pool=pool, llm_loader=load_llm)
    registry.llm_ready.connect(lambda: ready.append("llm"))

    assert registry.ensure_llm_loaded() is False
    _wait_until(lambda: ready == ["llm"], qapp, pool)
    assert llm_calls == [DEFAULT_WRITING_HELP_MODEL_KEY]

    assert registry.ensure_llm_loaded() is True
    assert ready == ["llm", "llm"]
    assert llm_calls == [DEFAULT_WRITING_HELP_MODEL_KEY]


@pytest.mark.parametrize(
    ("selected_key", "error_factory"),
    [
        pytest.param(DEFAULT_WRITING_HELP_MODEL_KEY, RuntimeError, id="default-writing-help-runtimeerror"),
        pytest.param(WRITING_HELP_PLUS_MODEL_KEY, RuntimeError, id="writing-help-plus-runtimeerror"),
    ],
)
def test_model_registry_llm_falls_back_on_runtime_error(
    tmp_path: Path,
    qapp: QApplication,
    selected_key: str,
    error_factory,
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    llm_calls: list[str] = []
    ready: list[str] = []
    fallbacks: list[tuple[str, str]] = []

    def load_llm(source, _path):
        llm_calls.append(source.key)
        if source.key == selected_key:
            raise error_factory(f"load failed for {source.key}")
        if source.key == FALLBACK_WRITING_HELP_MODEL_KEY:
            return f"loaded:{source.key}"
        raise AssertionError(f"Unexpected source {source.key}")

    registry = ModelRegistry(
        config,
        thread_pool=pool,
        llm_loader=load_llm,
        llm_model_key=selected_key,
    )
    registry.llm_ready.connect(lambda: ready.append("llm"))
    registry.llm_fallback.connect(lambda requested, fallback: fallbacks.append((requested, fallback)))

    assert registry.ensure_llm_loaded() is False
    _wait_until(lambda: ready == ["llm"], qapp, pool)

    assert llm_calls == [selected_key, FALLBACK_WRITING_HELP_MODEL_KEY]
    assert registry.llm == f"loaded:{FALLBACK_WRITING_HELP_MODEL_KEY}"
    assert registry.loaded_llm_key == FALLBACK_WRITING_HELP_MODEL_KEY
    assert fallbacks == [
        (
            model_source(selected_key).display,
            model_source(FALLBACK_WRITING_HELP_MODEL_KEY).display,
        )
    ]


def test_model_registry_emits_load_error_when_selected_llm_is_missing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    llm_calls: list[str] = []
    errors: list[tuple[str, str]] = []
    fallbacks: list[tuple[str, str]] = []

    def load_llm(source, _path):
        llm_calls.append(source.key)
        raise FileNotFoundError(f"missing {source.key}")

    registry = ModelRegistry(config, thread_pool=pool, llm_loader=load_llm)
    registry.load_error.connect(lambda model_name, message: errors.append((model_name, message)))
    registry.llm_fallback.connect(lambda requested, fallback: fallbacks.append((requested, fallback)))

    assert registry.ensure_llm_loaded() is False
    _wait_until(lambda: len(errors) == 1, qapp, pool)

    assert llm_calls == [DEFAULT_WRITING_HELP_MODEL_KEY]
    assert errors == [("Writing help", "missing gemma-4-E2B-it-Q8_0")]
    assert fallbacks == []
    assert registry.llm is None


def test_model_registry_emits_load_error_when_llm_fallback_fails(
    tmp_path: Path, qapp: QApplication
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    errors: list[tuple[str, str]] = []

    def load_llm(source, _path):
        raise RuntimeError(f"load failed for {source.key}")

    registry = ModelRegistry(config, thread_pool=pool, llm_loader=load_llm)
    registry.load_error.connect(lambda model_name, message: errors.append((model_name, message)))

    assert registry.ensure_llm_loaded() is False
    _wait_until(lambda: len(errors) == 1, qapp, pool)

    assert errors[0][0] == "Writing help"
    assert "load failed for gemma-4-E2B-it-Q8_0" in errors[0][1]
    assert f"{model_source(FALLBACK_WRITING_HELP_MODEL_KEY).display} also failed" in errors[0][1]
    assert registry.llm is None


def test_model_registry_emits_load_error_when_tts_loader_fails(
    tmp_path: Path, qapp: QApplication
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    errors: list[tuple[str, str]] = []

    def load_tts(source, _path):
        raise RuntimeError(f"load failed for {source.key}")

    registry = ModelRegistry(
        config,
        thread_pool=pool,
        whisper_loader=lambda _source, _path: "whisper",
        llm_loader=lambda _source, _path: "llm",
        tts_loader=load_tts,
    )
    registry.load_error.connect(lambda model_name, message: errors.append((model_name, message)))

    registry.load_all()
    _wait_until(lambda: len(errors) == 1, qapp, pool)

    assert errors == [("Voice replies", f"load failed for {VOICE_REPLY_MODEL_KEY}")]
    assert registry.tts is None


def test_model_registry_does_not_emit_fallback_when_selected_model_loads(
    tmp_path: Path, qapp: QApplication
) -> None:
    config = _make_config(tmp_path)
    pool = QThreadPool()
    fallbacks: list[tuple[str, str]] = []
    ready: list[str] = []

    registry = ModelRegistry(
        config,
        thread_pool=pool,
        llm_loader=lambda source, _path: f"loaded:{source.key}",
    )
    registry.llm_fallback.connect(lambda requested, fallback: fallbacks.append((requested, fallback)))
    registry.llm_ready.connect(lambda: ready.append("llm"))

    assert registry.ensure_llm_loaded() is False
    _wait_until(lambda: ready == ["llm"], qapp, pool)

    assert fallbacks == []
    assert registry.loaded_llm_key == DEFAULT_WRITING_HELP_MODEL_KEY