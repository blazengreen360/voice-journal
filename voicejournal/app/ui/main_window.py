from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VoiceJournal")
        self.resize(1200, 800)

        placeholder = QLabel(
            "VoiceJournal scaffold placeholder\n"
            "Home screen and repository wiring are not implemented yet."
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(placeholder)
