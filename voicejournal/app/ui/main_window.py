from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QStackedWidget

from voicejournal.app.model_sources import (
    FALLBACK_WRITING_HELP_MODEL_KEY,
    WRITING_HELP_PLUS_MODEL_KEY,
    model_source,
)
from voicejournal.app.ui.home_screen import HomeScreen
from voicejournal.app.ui.review_screen import ReviewScreen
from voicejournal.app.ui.session_screen import SessionScreen
from voicejournal.app.ui.settings_dialog import SettingsDialog


LLM_UNLOAD_INTERVAL_MS = 180_000
MODEL_LOADING_BANNER_TEXT = "Getting ready…"


class MainWindow(QMainWindow):
    def __init__(self, *, config=None, repo=None, registry=None) -> None:
        super().__init__()
        self.config = config
        self.repo = repo
        self.registry = registry
        self.setWindowTitle("VoiceJournal")
        self.resize(1200, 800)

        self._settings_dialog: SettingsDialog | None = None
        self._pending_session_start = False
        self._stack = QStackedWidget(self)
        llm_engine = getattr(self.registry, "llm", None)

        self._llm_unload_timer = QTimer(self)
        self._llm_unload_timer.setSingleShot(True)
        self._llm_unload_timer.setInterval(LLM_UNLOAD_INTERVAL_MS)
        if self.registry is not None and hasattr(self.registry, "unload_llm"):
            self._llm_unload_timer.timeout.connect(self.registry.unload_llm)

        self.home_screen = HomeScreen(repo=self.repo, parent=self)
        self.session_screen = SessionScreen(
            llm_engine=llm_engine,
            tts_engine=getattr(self.registry, "tts", None),
            transcriber=getattr(self.registry, "transcriber", None),
            config=self.config,
            parent=self,
        )
        self.review_screen = ReviewScreen(
            config=self.config,
            repo=self.repo,
            llm_engine=llm_engine,
            parent=self,
        )

        self._stack.addWidget(self.home_screen)
        self._stack.addWidget(self.session_screen)
        self._stack.addWidget(self.review_screen)
        self.setCentralWidget(self._stack)

        self.home_screen.new_entry_requested.connect(self._start_new_entry)
        self.home_screen.settings_requested.connect(self._open_settings)
        self.session_screen.cancel_requested.connect(self._show_home)
        self.session_screen.finish_requested.connect(self._show_review)
        self.review_screen.saved.connect(self._on_entry_saved)

        self._new_entry_action = QAction("New Entry", self)
        self._new_entry_action.setShortcuts(QKeySequence.StandardKey.New)
        self._new_entry_action.triggered.connect(self._activate_new_entry)
        self.addAction(self._new_entry_action)

        self._sync_home_model_state()
        self._connect_registry_signals()

    def _activate_new_entry(self) -> None:
        if self._stack.currentWidget() is self.home_screen:
            self.home_screen.activate_new_entry()

    def _sync_home_model_state(self) -> None:
        if self.registry is None or not hasattr(self.registry, "all_loaded"):
            self.home_screen.set_models_state("ready")
            return

        self.home_screen.set_models_state("ready" if self.registry.all_loaded else "loading")

    def _connect_registry_signals(self) -> None:
        if self.registry is None:
            return

        all_ready = getattr(self.registry, "all_ready", None)
        if all_ready is not None:
            all_ready.connect(self._on_all_ready)

        llm_ready = getattr(self.registry, "llm_ready", None)
        if llm_ready is not None:
            llm_ready.connect(self._on_llm_ready)

        tts_ready = getattr(self.registry, "tts_ready", None)
        if tts_ready is not None:
            tts_ready.connect(self._on_tts_ready)

        load_error = getattr(self.registry, "load_error", None)
        if load_error is not None:
            load_error.connect(self._on_model_load_error)

        llm_fallback = getattr(self.registry, "llm_fallback", None)
        if llm_fallback is not None:
            llm_fallback.connect(
                lambda requested, fallback: self.home_screen.show_info_banner(
                    f"Using {fallback} for now because {requested} could not load."
                )
            )

    def _on_model_load_error(self, model_name: str, message: str) -> None:
        self._pending_session_start = False
        any_loaded = any(
            getattr(self.registry, attribute, None) is not None
            for attribute in ("transcriber", "llm", "tts")
        )
        self.home_screen.show_model_issue(model_name, message, partial=any_loaded)

    def _on_all_ready(self) -> None:
        should_clear_loading_banner = (
            self.home_screen.models_state == "loading"
            and self.home_screen.banner_text == MODEL_LOADING_BANNER_TEXT
        )
        if self.home_screen.models_state in {"missing", "partial"} or should_clear_loading_banner:
            self.home_screen.clear_banner()
        self.home_screen.set_models_state("ready")
        if self._pending_session_start:
            self._pending_session_start = False
            self._open_session()

    def _start_new_entry(self) -> None:
        if self.registry is not None and hasattr(self.registry, "all_loaded"):
            if not self.registry.all_loaded:
                self._pending_session_start = True
                self.home_screen.show_info_banner(MODEL_LOADING_BANNER_TEXT)
                self.home_screen.set_models_state("loading")
                if hasattr(self.registry, "load_all"):
                    self.registry.load_all()
                return

        self._open_session()

    def _show_review(self, session, photo_paths) -> None:
        self.review_screen.begin_generation(session, photo_paths)
        self._stack.setCurrentWidget(self.review_screen)

    def _show_home(self) -> None:
        self._llm_unload_timer.start()
        self.home_screen.reload_entries()
        self._stack.setCurrentWidget(self.home_screen)

    def _on_entry_saved(self, _entry_id: str) -> None:
        self._llm_unload_timer.start()
        self.home_screen.reload_entries()
        self._stack.setCurrentWidget(self.home_screen)

    def _open_settings(self) -> None:
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(config=self.config, parent=self)
            self._settings_dialog.download_finished.connect(self._on_model_download_finished)
        self._settings_dialog.show_downloads()

    def _on_model_download_finished(self, _key: str) -> None:
        if self.registry is None or not hasattr(self.registry, "load_all"):
            return

        if _key == WRITING_HELP_PLUS_MODEL_KEY:
            self.home_screen.show_info_banner(
                f"{model_source(_key).display} is installed and will be available the next time you start VoiceJournal."
            )
            return

        if _key == FALLBACK_WRITING_HELP_MODEL_KEY and getattr(self.registry, "all_loaded", False):
            self.home_screen.show_info_banner(
                f"{model_source(_key).display} is installed and will be available the next time you start VoiceJournal."
            )
            return

        if not getattr(self.registry, "all_loaded", False):
            self.home_screen.show_info_banner(MODEL_LOADING_BANNER_TEXT)
            self.home_screen.set_models_state("loading")
        self.registry.load_all()

    def _on_llm_ready(self) -> None:
        llm_engine = getattr(self.registry, "llm", None)
        self.session_screen.set_llm_engine(llm_engine)
        self.session_screen.set_tts_engine(getattr(self.registry, "tts", None))
        self.session_screen.set_transcriber(getattr(self.registry, "transcriber", None))
        self.review_screen.set_llm_engine(llm_engine)

    def _on_tts_ready(self) -> None:
        self.session_screen.set_tts_engine(getattr(self.registry, "tts", None))

    def _open_session(self) -> None:
        self._llm_unload_timer.stop()
        llm_engine = getattr(self.registry, "llm", None)
        self.session_screen.set_llm_engine(llm_engine)
        self.session_screen.set_tts_engine(getattr(self.registry, "tts", None))
        self.session_screen.set_transcriber(getattr(self.registry, "transcriber", None))
        self.review_screen.set_llm_engine(llm_engine)
        self.home_screen.clear_banner()
        self.home_screen.set_models_state("ready")
        self.session_screen.start_session()
        self._stack.setCurrentWidget(self.session_screen)
