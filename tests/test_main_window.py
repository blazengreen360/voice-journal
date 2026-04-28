from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QStackedWidget

from voicejournal.app.config import AppPaths
from voicejournal.app.core.local_repo import LocalRepo
from voicejournal.app.core.llm import MetadataResult, QuestionResult
from voicejournal.app.model_sources import (
    DEFAULT_WRITING_HELP_MODEL_KEY,
    FALLBACK_WRITING_HELP_MODEL_KEY,
    WRITING_HELP_PLUS_MODEL_KEY,
)
from voicejournal.app.models.entry import ActiveSession
from voicejournal.app.ui.main_window import MainWindow


@dataclass(frozen=True, slots=True)
class _FakeConfig:
    _paths: AppPaths

    def paths(self) -> AppPaths:
        return self._paths


class _FakeLLM:
    def __init__(self) -> None:
        self._question_calls = 0

    def question(self, _messages):
        self._question_calls += 1
        summarize = self._question_calls >= 3
        return QuestionResult(
            next_question="Tell me a little more about that.",
            summarize=summarize,
            summarize_probability=0.9 if summarize else 0.3,
        )

    def stream_prose(self, _messages):
        yield "<p>Hello"
        yield " world</p>"

    def stream_metadata(self, _messages):
        yield '{"title":"A quiet evening",'
        yield '"mood":"Reflective",'
        yield '"tags":["home","quiet"]}'

    def parse_metadata(self, _raw_text: str):
        return MetadataResult(title="A quiet evening", mood="Reflective", tags=["home", "quiet"])


class _FakeRegistry(QObject):
    all_ready = Signal()
    llm_ready = Signal()
    tts_ready = Signal()
    load_error = Signal(str, str)
    llm_fallback = Signal(str, str)

    def __init__(self, *, llm, all_loaded: bool = True) -> None:
        super().__init__()
        self.llm = llm
        self.transcriber = object() if all_loaded else None
        self.tts = object() if all_loaded else None
        self._all_loaded = all_loaded
        self.ensure_calls = 0
        self.load_all_calls = 0
        self.unload_calls = 0

    @property
    def all_loaded(self) -> bool:
        return self._all_loaded

    def ensure_llm_loaded(self) -> bool:
        self.ensure_calls += 1
        return self.llm is not None

    def unload_llm(self) -> None:
        self.unload_calls += 1
        self.llm = None
        self._all_loaded = False

    def load_all(self) -> None:
        self.load_all_calls += 1


def _make_config(tmp_path: Path) -> _FakeConfig:
    paths = AppPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        logs_dir=tmp_path / "data" / "logs",
        models_dir=tmp_path / "models",
        photos_dir=tmp_path / "data" / "photos",
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


def _wait_until(app: QApplication, predicate, timeout_ms: int = 5000) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
    assert predicate()


def test_main_window_runs_home_to_session_to_review_to_save_flow(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=_FakeLLM())

    window = MainWindow(config=config, repo=repo, registry=registry)
    try:
        assert isinstance(window.centralWidget(), QStackedWidget)
        assert window.windowTitle() == "VoiceJournal"

        window.home_screen.activate_new_entry()
        assert window.centralWidget().currentWidget() is window.session_screen

        for text in ("One", "Two", "Three"):
            window.session_screen.turn_input.setText(text)
            window.session_screen.submit_user_turn()
            _wait_until(app, lambda: window.session_screen.turn_input.isEnabled())

        session = window.session_screen.active_session
        assert isinstance(session, ActiveSession)
        window.session_screen.finish_entry()
        assert window.centralWidget().currentWidget() is window.review_screen

        window.review_screen.save_action.trigger()
        _wait_until(
            app,
            lambda: window.centralWidget().currentWidget() is window.home_screen and repo.get_entry(session.entry_id) is not None,
        )

        assert repo.get_entry(session.entry_id) is not None
        assert window.home_screen.entry_list.count() >= 1
    finally:
        repo.close()
        window.close()


def test_main_window_waits_for_all_ready_before_opening_pending_session_after_eviction(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=None, all_loaded=False)
    registry.transcriber = object()
    registry.tts = object()
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window.home_screen.set_models_state("ready")
        window.home_screen.activate_new_entry()

        assert registry.load_all_calls == 1
        assert window.centralWidget().currentWidget() is window.home_screen
        assert window.home_screen.models_state == "loading"

        registry.llm = _FakeLLM()
        registry._all_loaded = True
        registry.llm_ready.emit()

        assert window.centralWidget().currentWidget() is window.home_screen
        assert window.home_screen.models_state == "loading"

        registry.all_ready.emit()

        assert window.centralWidget().currentWidget() is window.session_screen

        window._show_home()
        assert window._llm_unload_timer.isActive() is True
    finally:
        repo.close()
        window.close()


def test_main_window_does_not_auto_open_session_after_load_error_then_retry(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=None, all_loaded=False)
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window.home_screen.set_models_state("ready")
        window.home_screen.activate_new_entry()

        assert registry.load_all_calls == 1
        assert window.centralWidget().currentWidget() is window.home_screen
        assert window.home_screen.models_state == "loading"

        registry.load_error.emit("Voice replies", "missing files")
        app.processEvents()

        assert window.centralWidget().currentWidget() is window.home_screen
        assert window.home_screen.models_state == "missing"

        registry.transcriber = object()
        registry.llm = _FakeLLM()
        registry.tts = object()
        registry._all_loaded = True
        registry.all_ready.emit()
        app.processEvents()

        assert window.centralWidget().currentWidget() is window.home_screen
    finally:
        repo.close()
        window.close()


def test_main_window_updates_session_tts_when_tts_ready_fires(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=_FakeLLM(), all_loaded=False)
    registry.transcriber = object()
    registry.tts = None
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        assert window.session_screen._tts_engine is None

        registry.tts = object()
        registry.tts_ready.emit()
        app.processEvents()

        assert window.session_screen._tts_engine is registry.tts
    finally:
        repo.close()
        window.close()


def test_main_window_clears_partial_error_banner_when_models_recover(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=None, all_loaded=False)
    registry.transcriber = object()
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        registry.load_error.emit("Writing help", "missing files")
        app.processEvents()

        assert window.home_screen.models_state == "partial"
        assert window.home_screen.banner_label.isHidden() is False

        registry.llm = _FakeLLM()
        registry.tts = object()
        registry._all_loaded = True
        registry.all_ready.emit()
        app.processEvents()

        assert window.home_screen.models_state == "ready"
        assert window.home_screen.banner_label.isHidden() is True
    finally:
        repo.close()
        window.close()


def test_main_window_cancel_returns_home_and_restarts_timer(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=_FakeLLM())
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window.home_screen.activate_new_entry()
        assert window.centralWidget().currentWidget() is window.session_screen

        window.session_screen.cancel_requested.emit()
        assert window.centralWidget().currentWidget() is window.home_screen
        assert window._llm_unload_timer.isActive() is True

        window.home_screen.activate_new_entry()
        assert window.centralWidget().currentWidget() is window.session_screen
        assert window._llm_unload_timer.isActive() is False
    finally:
        repo.close()
        window.close()


def test_main_window_retries_model_loads_after_download_finish(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=None, all_loaded=False)
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window._open_settings()
        assert window._settings_dialog is not None

        window._settings_dialog.download_finished.emit(DEFAULT_WRITING_HELP_MODEL_KEY)
        app.processEvents()

        assert registry.load_all_calls == 1
        assert window.home_screen.models_state == "loading"
        assert window.home_screen.banner_text == "Getting ready…"

        registry.transcriber = object()
        registry.llm = _FakeLLM()
        registry.tts = object()
        registry._all_loaded = True
        registry.all_ready.emit()
        app.processEvents()

        assert window.home_screen.models_state == "ready"
        assert window.home_screen.banner_label.isHidden() is True
    finally:
        repo.close()
        window.close()


def test_main_window_download_finish_keeps_ready_state_when_models_are_loaded(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=_FakeLLM(), all_loaded=True)
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window._open_settings()
        assert window._settings_dialog is not None

        window._settings_dialog.download_finished.emit(WRITING_HELP_PLUS_MODEL_KEY)
        app.processEvents()

        assert registry.load_all_calls == 0
        assert window.home_screen.models_state == "ready"
        assert (
            window.home_screen.banner_text
            == "Writing help Plus is installed and will be available the next time you start VoiceJournal."
        )
    finally:
        repo.close()
        window.close()


def test_main_window_plus_download_shows_restart_banner_even_before_all_models_load(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=None, all_loaded=False)
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window._open_settings()
        assert window._settings_dialog is not None

        window._settings_dialog.download_finished.emit(WRITING_HELP_PLUS_MODEL_KEY)
        app.processEvents()

        assert registry.load_all_calls == 0
        assert window.home_screen.models_state == "loading"
        assert (
            window.home_screen.banner_text
            == "Writing help Plus is installed and will be available the next time you start VoiceJournal."
        )
    finally:
        repo.close()
        window.close()


def test_main_window_fallback_download_shows_restart_banner_when_models_are_loaded(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=_FakeLLM(), all_loaded=True)
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window._open_settings()
        assert window._settings_dialog is not None

        window._settings_dialog.download_finished.emit(FALLBACK_WRITING_HELP_MODEL_KEY)
        app.processEvents()

        assert registry.load_all_calls == 0
        assert (
            window.home_screen.banner_text
            == "Writing help fallback is installed and will be available the next time you start VoiceJournal."
        )
    finally:
        repo.close()
        window.close()


def test_main_window_fallback_download_retries_loads_when_models_are_not_ready(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    registry = _FakeRegistry(llm=None, all_loaded=False)
    window = MainWindow(config=config, repo=repo, registry=registry)

    try:
        window._open_settings()
        assert window._settings_dialog is not None

        window._settings_dialog.download_finished.emit(FALLBACK_WRITING_HELP_MODEL_KEY)
        app.processEvents()

        assert registry.load_all_calls == 1
        assert window.home_screen.models_state == "loading"
        assert window.home_screen.banner_text == "Getting ready…"
    finally:
        repo.close()
        window.close()