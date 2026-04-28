from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from voicejournal.app.config import AppPaths
from voicejournal.app.model_sources import (
    DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
    DEFAULT_WRITING_HELP_MODEL_KEY,
    FALLBACK_WRITING_HELP_MODEL_KEY,
)
from voicejournal.app.ui.settings_dialog import SettingsDialog


@dataclass(frozen=True, slots=True)
class _FakeConfig:
    _paths: AppPaths

    def paths(self) -> AppPaths:
        return self._paths


class _FakeDownloader(QObject):
    progress = Signal(str, object, object)
    finished = Signal(str)
    failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.enqueued: list[str] = []
        self.cancel_calls = 0
        self.cancel_keys: list[str | None] = []

    def enqueue(self, source) -> None:
        self.enqueued.append(source.key)

    def cancel(self, _key: str | None = None) -> None:
        self.cancel_calls += 1
        self.cancel_keys.append(_key)


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


def test_settings_dialog_starts_download_and_marks_model_installed(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    downloader = _FakeDownloader()
    dialog = SettingsDialog(config=config, downloader=downloader)
    finished: list[str] = []
    dialog.download_finished.connect(lambda key: finished.append(key))

    try:
        row = dialog.download_rows[DEFAULT_WRITING_HELP_MODEL_KEY]

        assert row.action_button.text() == "Download"

        row.action_button.click()
        assert downloader.enqueued == [DEFAULT_WRITING_HELP_MODEL_KEY]
        assert row.action_button.text() == "Cancel"

        downloader.progress.emit(DEFAULT_WRITING_HELP_MODEL_KEY, 2_483_747_296, 4_967_494_592)
        app.processEvents()
        assert row.progress_bar.value() == 50

        (config.paths().models_dir / "gemma-4-E2B-it-Q8_0.gguf").write_bytes(b"ok")
        downloader.finished.emit(DEFAULT_WRITING_HELP_MODEL_KEY)
        app.processEvents()

        assert row.action_button.text() == "Installed"
        assert "Installed" in row.meta_label.text()
        assert finished == [DEFAULT_WRITING_HELP_MODEL_KEY]
    finally:
        dialog.close()
        app.processEvents()


def test_settings_dialog_includes_backup_writing_model_row(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    dialog = SettingsDialog(config=config, downloader=_FakeDownloader())

    try:
        assert FALLBACK_WRITING_HELP_MODEL_KEY in dialog.download_rows
    finally:
        dialog.close()
        app.processEvents()


def test_settings_dialog_shows_resume_for_partial_download(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    downloader = _FakeDownloader()
    partial_path = config.paths().models_dir / ".gemma-4-E2B-it-Q8_0.gguf.partial"
    partial_path.write_bytes(b"partial-bytes")
    dialog = SettingsDialog(config=config, downloader=downloader)

    try:
        row = dialog.download_rows[DEFAULT_WRITING_HELP_MODEL_KEY]

        assert row.action_button.text() == "Resume"
        assert "Resume download" in row.meta_label.text()

        row.action_button.click()
        assert downloader.enqueued == [DEFAULT_WRITING_HELP_MODEL_KEY]
        assert row.action_button.text() == "Cancel"
    finally:
        dialog.close()
        app.processEvents()


def test_settings_dialog_disables_cancel_while_pause_is_in_flight(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    downloader = _FakeDownloader()
    partial_path = config.paths().models_dir / ".gemma-4-E2B-it-Q8_0.gguf.partial"
    partial_path.write_bytes(b"partial-bytes")
    dialog = SettingsDialog(config=config, downloader=downloader)

    try:
        row = dialog.download_rows[DEFAULT_WRITING_HELP_MODEL_KEY]

        row.action_button.click()
        row.action_button.click()

        assert downloader.cancel_calls == 1
        assert downloader.cancel_keys == [DEFAULT_WRITING_HELP_MODEL_KEY]
        assert row.action_button.isEnabled() is False
        assert row.action_button.text() == "Stopping..."

        downloader.failed.emit(DEFAULT_WRITING_HELP_MODEL_KEY, "Cancelled")
        app.processEvents()

        assert row.action_button.text() == "Resume"
        assert row.action_button.isEnabled() is True
    finally:
        dialog.close()
        app.processEvents()


def test_settings_dialog_cancel_promotes_next_queued_download(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    downloader = _FakeDownloader()
    dialog = SettingsDialog(config=config, downloader=downloader)

    try:
        writing_row = dialog.download_rows[DEFAULT_WRITING_HELP_MODEL_KEY]
        speech_row = dialog.download_rows[DEFAULT_SPEECH_RECOGNITION_MODEL_KEY]

        writing_row.action_button.click()
        speech_row.action_button.click()
        writing_row.action_button.click()

        assert downloader.cancel_keys == [DEFAULT_WRITING_HELP_MODEL_KEY]

        downloader.failed.emit(DEFAULT_WRITING_HELP_MODEL_KEY, "Cancelled")
        app.processEvents()

        assert speech_row.action_button.text() == "Cancel"
        assert speech_row.action_button.isEnabled() is True
        assert writing_row.action_button.text() == "Add to queue"
    finally:
        dialog.close()
        app.processEvents()


def test_settings_dialog_disables_cancel_after_complete_progress_until_finish(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    downloader = _FakeDownloader()
    dialog = SettingsDialog(config=config, downloader=downloader)

    try:
        row = dialog.download_rows[DEFAULT_WRITING_HELP_MODEL_KEY]

        row.action_button.click()
        downloader.progress.emit(DEFAULT_WRITING_HELP_MODEL_KEY, 4_967_494_592, 4_967_494_592)
        app.processEvents()

        assert row.action_button.isEnabled() is False
        assert row.action_button.text() == "Finishing..."
    finally:
        dialog.close()
        app.processEvents()


def test_settings_dialog_queues_next_download_and_promotes_it(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    downloader = _FakeDownloader()
    dialog = SettingsDialog(config=config, downloader=downloader)

    try:
        writing_row = dialog.download_rows[DEFAULT_WRITING_HELP_MODEL_KEY]
        speech_row = dialog.download_rows[DEFAULT_SPEECH_RECOGNITION_MODEL_KEY]

        writing_row.action_button.click()
        assert speech_row.action_button.text() == "Add to queue"

        speech_row.action_button.click()
        assert downloader.enqueued == [
            DEFAULT_WRITING_HELP_MODEL_KEY,
            DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
        ]
        assert speech_row.action_button.text() == "Queued"

        (config.paths().models_dir / "gemma-4-E2B-it-Q8_0.gguf").write_bytes(b"ok")

        downloader.finished.emit(DEFAULT_WRITING_HELP_MODEL_KEY)
        app.processEvents()

        assert speech_row.action_button.text() == "Cancel"
    finally:
        dialog.close()
        app.processEvents()