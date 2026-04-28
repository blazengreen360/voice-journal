from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


MODEL_STATES = frozenset({"ready", "loading", "missing", "partial"})


class NewEntryButton(QPushButton):
    activated = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._models_state = "loading"
        self.clicked.connect(self.activate)
        self.set_models_state("loading")

    @property
    def models_state(self) -> str:
        return self._models_state

    def set_models_state(self, state: str) -> None:
        if state not in MODEL_STATES:
            valid = ", ".join(sorted(MODEL_STATES))
            raise ValueError(f"Unsupported models state '{state}'. Expected one of: {valid}")

        self._models_state = state
        if state == "ready":
            self.setText("New Entry")
            self.setEnabled(True)
        elif state == "loading":
            self.setText("Loading models…")
            self.setEnabled(False)
        elif state == "partial":
            self.setText("Finish setup ->")
            self.setEnabled(True)
        elif state == "missing":
            self.setText("Set up models ->")
            self.setEnabled(True)
        else:
            self.setText("New Entry")
            self.setEnabled(False)

    def activate(self) -> None:
        if self._models_state == "ready":
            self.activated.emit()
        elif self._models_state in {"missing", "partial"}:
            self.settings_requested.emit()


class HomeToolBar(QToolBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Home", parent)
        self.setMovable(False)
        self.setFloatable(False)


class HomeScreen(QWidget):
    new_entry_requested = Signal()
    settings_requested = Signal()

    def __init__(self, *, repo=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._banner_text = ""

        self.toolbar = HomeToolBar(self)
        self.title_label = QLabel("Home")
        title_font = QFont()
        title_font.setPointSize(15)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.toolbar.addWidget(self.title_label)

        toolbar_spacer = QWidget(self)
        toolbar_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(toolbar_spacer)

        self.settings_button = QPushButton("Settings", self)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.toolbar.addWidget(self.settings_button)

        self.new_entry_button = NewEntryButton(self)
        self.new_entry_button.activated.connect(self.new_entry_requested.emit)
        self.new_entry_button.settings_requested.connect(self.settings_requested.emit)
        self.toolbar.addWidget(self.new_entry_button)

        self.banner_label = QLabel(self)
        self.banner_label.setWordWrap(True)
        self.banner_label.hide()

        self.banner_action_button = QPushButton("Open Settings", self)
        self.banner_action_button.clicked.connect(self.settings_requested.emit)
        self.banner_action_button.hide()

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Search your journal")
        self.search_input.textChanged.connect(self.reload_entries)

        self.calendar = QCalendarWidget(self)
        self.calendar.setGridVisible(False)
        self.calendar.selectionChanged.connect(self._update_selected_day_summary)
        self.calendar.currentPageChanged.connect(lambda _year, _month: self._refresh_calendar_marks())

        self.day_summary_label = QLabel(self)
        self.entry_list = QListWidget(self)
        self.empty_state_label = QLabel("Start your first entry when you're ready.", self)
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        body_layout = QHBoxLayout()
        body_layout.addWidget(self.calendar, 2)
        body_layout.addWidget(self.entry_list, 3)

        layout = QVBoxLayout(self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.banner_label)
        layout.addWidget(self.banner_action_button)
        layout.addWidget(self.search_input)
        layout.addWidget(self.day_summary_label)
        layout.addLayout(body_layout)
        layout.addWidget(self.empty_state_label)
        self.setLayout(layout)

        self.reload_entries()
        self._update_selected_day_summary()
        self._refresh_calendar_marks()

    @property
    def models_state(self) -> str:
        return self.new_entry_button.models_state

    @property
    def banner_text(self) -> str:
        return self._banner_text

    def activate_new_entry(self) -> None:
        self.new_entry_button.activate()

    def set_models_state(self, state: str) -> None:
        self.new_entry_button.set_models_state(state)

    def show_model_issue(self, model_name: str, message: str, *, partial: bool) -> None:
        self._banner_text = f"{model_name} needs attention. {message}"
        self.banner_label.setText(self._banner_text)
        self.banner_label.show()
        self.set_models_state("partial" if partial else "missing")
        self.banner_action_button.show()

    def show_info_banner(self, message: str) -> None:
        self._banner_text = message
        self.banner_label.setText(message)
        self.banner_label.show()
        self.banner_action_button.hide()

    def clear_banner(self) -> None:
        self._banner_text = ""
        self.banner_label.clear()
        self.banner_label.hide()
        self.banner_action_button.hide()

    def reload_entries(self) -> None:
        self.entry_list.clear()

        entries = []
        query = self.search_input.text().strip()
        if self._repo is not None and hasattr(self._repo, "search_entries"):
            entries = list(self._repo.search_entries(query, limit=50))

        for entry in entries:
            created = _entry_date_text(entry.created_at)
            title = entry.title.strip() or "Untitled entry"
            item = QListWidgetItem(f"{title}\n{created}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self.entry_list.addItem(item)

        is_empty = self.entry_list.count() == 0
        self.entry_list.setVisible(not is_empty)
        self.empty_state_label.setVisible(is_empty)
        self._update_selected_day_summary()
        self._refresh_calendar_marks()

    def _update_selected_day_summary(self) -> None:
        selected = self.calendar.selectedDate()
        count = 0
        if self._repo is not None and hasattr(self._repo, "all_entry_ids") and hasattr(self._repo, "get_entry"):
            for entry_id in self._repo.all_entry_ids():
                entry = self._repo.get_entry(entry_id)
                if entry is None:
                    continue
                created = _entry_qdate(entry.created_at)
                if created == selected:
                    count += 1
        elif self._repo is not None and hasattr(self._repo, "search_entries"):
            for entry in self._repo.search_entries("", limit=10_000):
                created = _entry_qdate(entry.created_at)
                if created == selected:
                    count += 1

        label = selected.toString("MMMM d")
        suffix = "entry" if count == 1 else "entries"
        self.day_summary_label.setText(f"{label} · {count} {suffix}")

    def _refresh_calendar_marks(self) -> None:
        year = self.calendar.yearShown()
        month = self.calendar.monthShown()
        default_format = QTextCharFormat()
        for day in range(1, 32):
            date = QDate(year, month, day)
            if date.isValid():
                self.calendar.setDateTextFormat(date, default_format)

        if self._repo is None or not hasattr(self._repo, "entry_days_in_month"):
            return

        marked_days = self._repo.entry_days_in_month(year, month)
        highlighted = QTextCharFormat()
        highlighted.setForeground(QColor("#2f6e49"))
        highlighted.setFontWeight(QFont.Weight.DemiBold)

        for day in marked_days:
            date = QDate(year, month, int(day))
            if date.isValid():
                self.calendar.setDateTextFormat(date, highlighted)


def _entry_date_text(value: str) -> str:
    return _entry_qdate(value).toString("MMM d, yyyy")


def _entry_qdate(value: str) -> QDate:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return QDate(parsed.year, parsed.month, parsed.day)


__all__ = ["HomeScreen", "HomeToolBar", "NewEntryButton"]