from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from voicejournal.app.model_downloader import DOWNLOAD_CANCELLED_MESSAGE, ModelDownloader
from voicejournal.app.model_sources import (
    DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
    DEFAULT_WRITING_HELP_MODEL_KEY,
    FALLBACK_WRITING_HELP_MODEL_KEY,
    UNPINNED_SHA256,
    VOICE_REPLY_MODEL_KEY,
    WRITING_HELP_PLUS_MODEL_KEY,
    ModelSource,
    model_source,
)


DOWNLOAD_MODEL_KEYS = (
    DEFAULT_SPEECH_RECOGNITION_MODEL_KEY,
    DEFAULT_WRITING_HELP_MODEL_KEY,
    FALLBACK_WRITING_HELP_MODEL_KEY,
    WRITING_HELP_PLUS_MODEL_KEY,
    VOICE_REPLY_MODEL_KEY,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(slots=True)
class DownloadRow:
    title_label: QLabel
    meta_label: QLabel
    progress_bar: QProgressBar
    action_button: QPushButton


class SettingsDialog(QDialog):
    download_finished = Signal(str)

    def __init__(self, *, config, downloader: ModelDownloader | None = None, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._models_dir = self._config.paths().models_dir
        self._downloader = downloader or ModelDownloader(self._models_dir)
        self._active_key: str | None = None
        self._cancelling_key: str | None = None
        self._queued_keys: list[str] = []
        self._progress: dict[str, tuple[int, int]] = {}
        self._row_errors: dict[str, str] = {}
        self.setWindowTitle("Settings")
        self.resize(720, 460)

        self._downloader.progress.connect(self._on_download_progress)
        self._downloader.finished.connect(self._on_download_finished)
        self._downloader.failed.connect(self._on_download_failed)

        self.tabs = QTabWidget(self)
        self.downloads_tab = QWidget(self)
        self.tabs.addTab(self.downloads_tab, "Downloads")

        downloads_layout = QVBoxLayout(self.downloads_tab)
        downloads_layout.addWidget(QLabel("Downloads", self.downloads_tab))
        downloads_layout.addWidget(
            QLabel(
                "Download Speech recognition, Writing help, and Voice replies to start a new entry. Writing help Plus is optional.",
                self.downloads_tab,
            )
        )
        downloads_layout.addWidget(
            QLabel(
                "If Writing help cannot load on this machine, Writing help fallback is the backup download.",
                self.downloads_tab,
            )
        )

        actions_row = QHBoxLayout()
        self.models_path_label = QLabel(str(self._models_dir), self.downloads_tab)
        self.models_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_models_folder_button = QPushButton("Open Models Folder", self.downloads_tab)
        self.open_models_folder_button.clicked.connect(self._open_models_folder)
        self.refresh_button = QPushButton("Refresh", self.downloads_tab)
        self.refresh_button.clicked.connect(self.refresh_model_rows)
        actions_row.addWidget(self.models_path_label, 1)
        actions_row.addWidget(self.open_models_folder_button)
        actions_row.addWidget(self.refresh_button)
        downloads_layout.addLayout(actions_row)

        self.download_rows: dict[str, DownloadRow] = {}
        for key in DOWNLOAD_MODEL_KEYS:
            row_widget = QWidget(self.downloads_tab)
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 8, 0, 8)

            header_layout = QHBoxLayout()
            title_label = QLabel(model_source(key).display, row_widget)
            action_button = QPushButton(row_widget)
            action_button.clicked.connect(lambda _checked=False, key=key: self._on_row_action(key))
            header_layout.addWidget(title_label)
            header_layout.addStretch(1)
            header_layout.addWidget(action_button)

            meta_label = QLabel(row_widget)
            meta_label.setWordWrap(True)
            progress_bar = QProgressBar(row_widget)
            progress_bar.setRange(0, 100)
            progress_bar.setValue(0)
            progress_bar.setFormat("%p%")

            row_layout.addLayout(header_layout)
            row_layout.addWidget(meta_label)
            row_layout.addWidget(progress_bar)

            self.download_rows[key] = DownloadRow(
                title_label=title_label,
                meta_label=meta_label,
                progress_bar=progress_bar,
                action_button=action_button,
            )
            downloads_layout.addWidget(row_widget)

        downloads_layout.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self.refresh_model_rows()

    def show_downloads(self) -> None:
        self.tabs.setCurrentWidget(self.downloads_tab)
        self.refresh_model_rows()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh_model_rows(self) -> None:
        for key in self.download_rows:
            self._refresh_row(key)

    def _on_row_action(self, key: str) -> None:
        if key == self._active_key:
            self._cancelling_key = key
            row = self.download_rows[key]
            row.meta_label.setText("Stopping download...")
            row.action_button.setText("Stopping...")
            row.action_button.setEnabled(False)
            self._downloader.cancel(key)
            return

        if key in self._queued_keys or _is_installed(self._models_dir, model_source(key)):
            return

        source = model_source(key)
        self._row_errors.pop(key, None)
        if self._active_key is None:
            self._active_key = key
        else:
            self._queued_keys.append(key)
        self._downloader.enqueue(source)
        self.refresh_model_rows()

    def _on_download_progress(self, key: str, done, total) -> None:
        self._progress[key] = (int(done), int(total))
        self._refresh_row(key)

    def _on_download_finished(self, key: str) -> None:
        self._row_errors.pop(key, None)
        if self._cancelling_key == key:
            self._cancelling_key = None
        source = model_source(key)
        self._progress[key] = (source.size_bytes, source.size_bytes)
        self._promote_next_queued(key)
        self.refresh_model_rows()
        self.download_finished.emit(key)

    def _on_download_failed(self, key: str, message: str) -> None:
        if self._cancelling_key == key:
            self._cancelling_key = None
        if message == DOWNLOAD_CANCELLED_MESSAGE:
            self._row_errors[key] = "Download paused. You can resume when you are ready."
        elif "Hash mismatch" in message:
            self._row_errors[key] = "Verification failed. Try the download again."
        else:
            self._row_errors[key] = f"Could not finish download. {message}"
        self._promote_next_queued(key)
        self.refresh_model_rows()

    def _promote_next_queued(self, finished_key: str) -> None:
        if self._active_key != finished_key:
            return
        self._active_key = self._queued_keys.pop(0) if self._queued_keys else None

    def _open_models_folder(self) -> None:
        self._models_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._models_dir)))

    def _refresh_row(self, key: str) -> None:
        source = model_source(key)
        row = self.download_rows[key]
        installed = _is_installed(self._models_dir, source)
        downloaded_bytes = _downloaded_bytes(self._models_dir, source)
        progress_done, progress_total = self._progress.get(key, (0, source.size_bytes))

        if installed:
            row.meta_label.setText(f"Installed · {_format_bytes(source.size_bytes)}")
            row.progress_bar.setValue(100)
            row.action_button.setText("Installed")
            row.action_button.setEnabled(False)
            return

        if not _source_is_pinned(source):
            row.meta_label.setText("Download unavailable until verification is pinned.")
            row.progress_bar.setValue(0)
            row.action_button.setText("Unavailable")
            row.action_button.setEnabled(False)
            return

        if key == self._active_key:
            if self._cancelling_key == key:
                row.meta_label.setText("Stopping download...")
                row.progress_bar.setValue(_progress_percent(progress_done, progress_total))
                row.action_button.setText("Stopping...")
                row.action_button.setEnabled(False)
                return
            if progress_total > 0 and progress_done >= progress_total:
                row.meta_label.setText("Finishing download...")
                row.progress_bar.setValue(100)
                row.action_button.setText("Finishing...")
                row.action_button.setEnabled(False)
                return
            if progress_done > 0 and progress_total > 0:
                row.meta_label.setText(
                    f"Downloading now · {_format_progress(progress_done, progress_total)}"
                )
                row.progress_bar.setValue(_progress_percent(progress_done, progress_total))
            else:
                row.meta_label.setText(f"Downloading now · {_format_bytes(source.size_bytes)}")
                row.progress_bar.setValue(_progress_percent(downloaded_bytes, source.size_bytes))
            row.action_button.setText("Cancel")
            row.action_button.setEnabled(True)
            return

        if key in self._queued_keys:
            row.meta_label.setText("Queued")
            row.progress_bar.setValue(_progress_percent(downloaded_bytes, source.size_bytes))
            row.action_button.setText("Queued")
            row.action_button.setEnabled(False)
            return

        error_message = self._row_errors.get(key)
        if error_message is not None:
            row.meta_label.setText(error_message)
        elif downloaded_bytes > 0:
            row.meta_label.setText(f"Resume download · {_format_progress(downloaded_bytes, source.size_bytes)}")
        else:
            row.meta_label.setText(f"Missing · {_format_bytes(source.size_bytes)}")

        row.progress_bar.setValue(_progress_percent(downloaded_bytes, source.size_bytes))
        row.action_button.setText(self._idle_action_text(key, downloaded_bytes > 0, error_message is not None))
        row.action_button.setEnabled(True)

    def _idle_action_text(self, key: str, has_partial: bool, has_error: bool) -> str:
        if self._active_key is not None:
            return "Add to queue"
        if has_partial:
            return "Resume"
        if has_error:
            return "Retry"
        return "Download"


def _is_installed(models_dir: Path, source) -> bool:
    if source.install_dir is None:
        return (models_dir / source.filename).is_file()

    install_dir = models_dir / source.install_dir
    return all((install_dir / artifact.filename).is_file() for artifact in source.artifacts)


def _downloaded_bytes(models_dir: Path, source: ModelSource) -> int:
    total = 0
    for artifact in source.artifacts:
        final_path = _final_path(models_dir, source, artifact.filename)
        if final_path.is_file():
            total += final_path.stat().st_size
        elif (partial_path := _partial_path(models_dir, source, artifact.filename)).is_file():
            total += partial_path.stat().st_size
    return total


def _final_path(models_dir: Path, source: ModelSource, filename: str) -> Path:
    install_root = models_dir if source.install_dir is None else models_dir / source.install_dir
    return install_root / filename


def _partial_path(models_dir: Path, source: ModelSource, filename: str) -> Path:
    install_root = models_dir if source.install_dir is None else models_dir / source.install_dir
    return install_root / f".{filename}.partial"


def _source_is_pinned(source: ModelSource) -> bool:
    return all(
        artifact.sha256 != UNPINNED_SHA256 and _SHA256_RE.fullmatch(artifact.sha256) is not None
        for artifact in source.artifacts
    )


def _progress_percent(done_bytes: int, total_bytes: int) -> int:
    if total_bytes <= 0:
        return 0
    return max(0, min(100, int((done_bytes / total_bytes) * 100)))


def _format_progress(done_bytes: int, total_bytes: int) -> str:
    return f"{_format_bytes(done_bytes)} of {_format_bytes(total_bytes)}"


def _format_bytes(total_bytes: int) -> str:
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    if total_bytes >= gib:
        return f"{total_bytes / gib:.1f} GB"
    return f"{total_bytes / mib:.1f} MB"


__all__ = ["SettingsDialog"]