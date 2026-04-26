from __future__ import annotations

import atexit
import os
import sys

from voicejournal.app.config import AppConfig
from voicejournal.app.packaging_smoke import run_packaging_smoke
from voicejournal.app.single_instance import AlreadyRunningError, SingleInstanceLock


def main() -> int:
    if os.environ.get("VOICEJOURNAL_SMOKE") == "1":
        return run_packaging_smoke(os.environ.get("VOICEJOURNAL_SMOKE_OUTPUT"))

    from PySide6.QtWidgets import QApplication, QMessageBox

    from voicejournal.app.ui.main_window import MainWindow

    qt_app = QApplication(sys.argv)

    config = AppConfig()
    paths = config.ensure_directories()

    lock = SingleInstanceLock(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError:
        QMessageBox.information(None, "VoiceJournal", "VoiceJournal is already running.")
        return 0

    atexit.register(lock.release)

    qt_app.setApplicationName("VoiceJournal")
    qt_app.setOrganizationName("VoiceJournal")

    window = MainWindow()
    window.show()
    return qt_app.exec()
