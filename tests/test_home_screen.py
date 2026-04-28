from __future__ import annotations

from PySide6.QtWidgets import QApplication

from voicejournal.app.models.entry import JournalEntry
from voicejournal.app.ui.home_screen import HomeScreen, _entry_qdate


class _FakeRepo:
    def __init__(self, entries: list[JournalEntry]) -> None:
        self._entries = {entry.id: entry for entry in entries}

    def search_entries(self, query: str, *, limit: int = 50) -> list[JournalEntry]:
        lowered = query.lower().strip()
        results = [entry for entry in self._entries.values() if lowered in entry.title.lower()]
        return results[:limit]

    def all_entry_ids(self) -> set[str]:
        return set(self._entries)

    def get_entry(self, entry_id: str) -> JournalEntry | None:
        return self._entries.get(entry_id)

    def entry_days_in_month(self, year: int, month: int) -> dict[int, str | None]:
        day_map: dict[int, str | None] = {}
        for entry in self._entries.values():
            date = entry.created_at.split("T", 1)[0]
            entry_year, entry_month, entry_day = [int(part) for part in date.split("-")]
            if entry_year == year and entry_month == month:
                day_map[entry_day] = entry.mood
        return day_map


def test_home_screen_loads_entries_and_updates_summary() -> None:
    app = QApplication.instance() or QApplication([])
    screen = HomeScreen(
        repo=_FakeRepo(
            [
                JournalEntry(title="A quiet evening", body="<p>One</p>", created_at="2026-04-27T10:00:00+00:00"),
                JournalEntry(title="Morning walk", body="<p>Two</p>", created_at="2026-04-27T12:00:00+00:00"),
            ]
        )
    )
    try:
        screen.reload_entries()
        screen.calendar.setSelectedDate(_entry_qdate("2026-04-27T10:00:00+00:00"))
        app.processEvents()

        assert screen.entry_list.count() == 2
        assert "2 entries" in screen.day_summary_label.text()
    finally:
        screen.close()
        app.processEvents()


def test_home_screen_new_entry_button_routes_ready_and_missing_states() -> None:
    app = QApplication.instance() or QApplication([])
    screen = HomeScreen(repo=_FakeRepo([]))
    fired: list[str] = []
    screen.new_entry_requested.connect(lambda: fired.append("new"))
    screen.settings_requested.connect(lambda: fired.append("settings"))

    try:
        screen.set_models_state("ready")
        screen.activate_new_entry()
        screen.set_models_state("missing")
        screen.activate_new_entry()

        assert fired == ["new", "settings"]
    finally:
        screen.close()
        app.processEvents()


def test_home_screen_partial_issue_exposes_settings_affordance() -> None:
    app = QApplication.instance() or QApplication([])
    screen = HomeScreen(repo=_FakeRepo([]))
    fired: list[str] = []
    screen.settings_requested.connect(lambda: fired.append("settings"))

    try:
        screen.show_model_issue("Writing help", "Download is incomplete.", partial=True)
        screen.banner_action_button.click()

        assert screen.models_state == "partial"
        assert screen.banner_action_button.isHidden() is False
        assert fired == ["settings"]
    finally:
        screen.close()
        app.processEvents()


def test_home_screen_partial_state_routes_new_entry_button_to_settings() -> None:
    app = QApplication.instance() or QApplication([])
    screen = HomeScreen(repo=_FakeRepo([]))
    fired: list[str] = []
    screen.settings_requested.connect(lambda: fired.append("settings"))

    try:
        screen.show_model_issue("Voice replies", "Download is incomplete.", partial=True)
        screen.activate_new_entry()

        assert screen.models_state == "partial"
        assert screen.new_entry_button.text() == "Finish setup ->"
        assert screen.new_entry_button.isEnabled() is True
        assert fired == ["settings"]
    finally:
        screen.close()
        app.processEvents()