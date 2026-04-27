from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

from voicejournal.app.models.entry import EntryPhoto, JournalEntry, SessionTurn


class JournalRepository(ABC):
    @abstractmethod
    def close(self) -> None:
        """Release any repository resources."""

    @abstractmethod
    def save_entry(
        self,
        entry: JournalEntry,
        *,
        turns: Sequence[SessionTurn] | None = None,
        photos: Sequence[EntryPhoto] | None = None,
    ) -> None:
        """Persist an entry and its related session turns and photos."""

    @abstractmethod
    def get_entry(self, entry_id: str) -> JournalEntry | None:
        """Return the requested entry, if present."""

    @abstractmethod
    def list_session_turns(self, entry_id: str) -> list[SessionTurn]:
        """Return persisted turns for an entry in turn order."""

    @abstractmethod
    def list_entry_photos(self, entry_id: str) -> list[EntryPhoto]:
        """Return persisted photos for an entry in display order."""

    @abstractmethod
    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry and its related rows."""

    @abstractmethod
    def search_entries(self, query: str, *, limit: int = 50) -> list[JournalEntry]:
        """Search persisted entries using the repository's full-text index."""

    @abstractmethod
    def all_entry_ids(self) -> set[str]:
        """Return all persisted entry ids."""

    @abstractmethod
    def update_photo_orders(self, entry_id: str, photo_ids: Sequence[str]) -> None:
        """Persist a new photo ordering for an entry."""

    @abstractmethod
    def entry_days_in_month(self, year: int, month: int) -> dict[int, str | None]:
        """Return month-day moods, where the most recent entry of the day wins."""

    @abstractmethod
    def import_photo_file(self, source_path: Path, photos_dir: Path, entry_id: str) -> str:
        """Copy a photo into the managed photos directory and return its relative path."""

    @abstractmethod
    def fts_integrity_ok(self) -> bool:
        """Return whether the FTS index is present and in sync with entries."""

    @abstractmethod
    def rebuild_fts(self) -> None:
        """Rebuild the FTS index from the entries table."""