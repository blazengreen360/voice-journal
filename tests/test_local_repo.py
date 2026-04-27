from __future__ import annotations

import html as html_module
from pathlib import Path, PureWindowsPath
import sqlite3
from urllib.parse import quote

import pytest

from voicejournal.app.core import local_repo as local_repo_module
from voicejournal.app.core.local_repo import LocalRepo, _open_connection
from voicejournal.app.models.entry import EntryPhoto, JournalEntry, SessionTurn


def _write_managed_photo(tmp_path: Path, entry_id: str, filename: str, content: bytes = b"jpg") -> str:
    photo_path = tmp_path / "photos" / entry_id / filename
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(content)
    return f"photos/{entry_id}/{filename}"


def test_open_connection_configures_wal_and_foreign_keys(tmp_path) -> None:
    conn = _open_connection(tmp_path / "journal.db")

    assert conn.row_factory is sqlite3.Row
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    conn.close()


def test_local_repo_persists_entry_turns_photos_and_searches_fts(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    sunrise_path = _write_managed_photo(tmp_path, "entry-1", "sunrise.jpg")
    tea_path = _write_managed_photo(tmp_path, "entry-1", "tea.jpg")
    entry = JournalEntry(
        id="entry-1",
        title="Morning reflection",
        body="<p>Hello <b>world</b> from VoiceJournal.</p>",
        created_at="2026-04-26T08:00:00+00:00",
        updated_at="2026-04-26T08:05:00+00:00",
        saved_at="2026-04-26T08:06:00+00:00",
        mood="calm",
        tags=["journal", "voice"],
        voice_name="Lucy",
    )
    turns = [
        SessionTurn(
            id="turn-1",
            entry_id=entry.id,
            turn_index=0,
            role="assistant",
            text="How are you feeling?",
            created_at="2026-04-26T08:00:01+00:00",
        ),
        SessionTurn(
            id="turn-2",
            entry_id=entry.id,
            turn_index=1,
            role="user",
            text="Grounded and optimistic.",
            created_at="2026-04-26T08:00:03+00:00",
        ),
    ]
    photos = [
        EntryPhoto(
            id="photo-1",
            entry_id=entry.id,
            file_path=sunrise_path,
            caption="Sunrise",
            sort_order=0,
            created_at="2026-04-26T08:00:04+00:00",
        ),
        EntryPhoto(
            id="photo-2",
            entry_id=entry.id,
            file_path=tea_path,
            caption="Tea",
            sort_order=1,
            created_at="2026-04-26T08:00:05+00:00",
        ),
    ]

    repo.save_entry(entry, turns=turns, photos=photos)

    loaded_entry = repo.get_entry(entry.id)
    assert loaded_entry == entry
    assert repo.list_session_turns(entry.id) == turns
    assert repo.list_entry_photos(entry.id) == photos
    assert [result.id for result in repo.search_entries("world")][0] == entry.id
    assert [result.id for result in repo.search_entries('"Morning reflection"')] == [entry.id]

    row = repo._conn.execute(
        "SELECT body_text, body_snippet FROM entries WHERE id = ?",
        (entry.id,),
    ).fetchone()
    assert row["body_text"] == "Hello world from VoiceJournal."
    assert row["body_snippet"] == "Hello world from VoiceJournal."

    repo.close()


def test_local_repo_save_entry_preserves_children_when_not_resupplied(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    original = JournalEntry(id="entry-1", title="Original", body="<p>Hello world</p>")
    turn = SessionTurn(id="turn-1", entry_id=original.id, turn_index=0, role="user", text="hello")
    photo = EntryPhoto(
        id="photo-1",
        entry_id=original.id,
        file_path=_write_managed_photo(tmp_path, original.id, "one.jpg"),
    )
    repo.save_entry(original, turns=[turn], photos=[photo])

    updated = JournalEntry(
        id=original.id,
        title="Updated",
        body="<p>Updated body</p>",
        created_at=original.created_at,
        updated_at="2026-04-26T10:00:00+00:00",
        saved_at="2026-04-26T10:01:00+00:00",
        mood="calm",
        tags=["edited"],
        voice_name="Lucy",
    )
    repo.save_entry(updated)

    assert repo.get_entry(original.id) == updated
    assert repo.list_session_turns(original.id) == [turn]
    assert repo.list_entry_photos(original.id) == [photo]

    repo.save_entry(updated, turns=[], photos=[])

    assert repo.list_session_turns(original.id) == []
    assert repo.list_entry_photos(original.id) == []

    repo.close()


def test_local_repo_save_entry_rejects_children_for_other_entry_ids(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(id="entry-1", title="Entry", body="<p>Body</p>")
    foreign_turn = SessionTurn(id="turn-1", entry_id="entry-2", turn_index=0, role="user", text="hi")
    foreign_photo = EntryPhoto(id="photo-1", entry_id="entry-2", file_path="photos/entry-2/one.jpg")

    with pytest.raises(ValueError, match="expected 'entry-1'"):
        repo.save_entry(entry, turns=[foreign_turn])

    with pytest.raises(ValueError, match="expected 'entry-1'"):
        repo.save_entry(entry, photos=[foreign_photo])

    assert repo.get_entry(entry.id) is None

    repo.close()


def test_local_repo_save_entry_rejects_invalid_photo_paths(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(id="entry-1", title="Entry", body="<p>Body</p>")
    invalid_photo = EntryPhoto(id="photo-1", entry_id="entry-1", file_path="photos/wrong-dir/pic.jpg")

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(entry, photos=[invalid_photo])

    assert repo.get_entry(entry.id) is None

    repo.close()


def test_local_repo_save_entry_rejects_missing_managed_photo_files(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(id="entry-1", title="Entry", body="<p>Body</p>")
    missing_photo = EntryPhoto(id="photo-1", entry_id="entry-1", file_path="photos/entry-1/missing.jpg")

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(entry, photos=[missing_photo])

    assert repo.get_entry(entry.id) is None

    repo.close()


def test_local_repo_delete_entry_cascades_related_rows(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(id="entry-1", title="Delete me", body="<p>Body</p>")
    turn = SessionTurn(id="turn-1", entry_id=entry.id, turn_index=0, role="user", text="hi")
    photo = EntryPhoto(
        id="photo-1",
        entry_id=entry.id,
        file_path=_write_managed_photo(tmp_path, entry.id, "one.jpg"),
    )
    repo.save_entry(entry, turns=[turn], photos=[photo])

    assert repo.delete_entry(entry.id) is True
    assert repo.get_entry(entry.id) is None
    assert repo.list_session_turns(entry.id) == []
    assert repo.list_entry_photos(entry.id) == []
    assert repo.all_entry_ids() == set()

    repo.close()


def test_local_repo_import_photo_file_is_collision_safe_and_updates_photo_order(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    photos_dir = tmp_path / "photos"
    source_path = tmp_path / "source.jpg"
    source_path.write_bytes(b"image-bytes")

    first_relative = repo.import_photo_file(source_path, photos_dir, "entry-1")
    second_relative = repo.import_photo_file(source_path, photos_dir, "entry-1")

    assert first_relative == "photos/entry-1/source.jpg"
    assert second_relative == "photos/entry-1/source-2.jpg"
    assert (tmp_path / first_relative).is_file()
    assert (tmp_path / second_relative).is_file()

    entry = JournalEntry(id="entry-1", title="Photos", body="<p>Body</p>")
    first_photo = EntryPhoto(id="photo-1", entry_id=entry.id, file_path=first_relative, sort_order=0)
    second_photo = EntryPhoto(id="photo-2", entry_id=entry.id, file_path=second_relative, sort_order=1)
    repo.save_entry(entry, photos=[first_photo, second_photo])

    repo.update_photo_orders(entry.id, [second_photo.id, first_photo.id])

    assert [photo.id for photo in repo.list_entry_photos(entry.id)] == [second_photo.id, first_photo.id]

    with pytest.raises(ValueError, match="exactly once"):
        repo.update_photo_orders(entry.id, [second_photo.id])

    with pytest.raises(ValueError, match="duplicate"):
        repo.update_photo_orders(entry.id, [second_photo.id, second_photo.id])

    repo.close()


def test_local_repo_entry_days_in_month_uses_most_recent_mood(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    first = JournalEntry(
        id="entry-1",
        title="First",
        body="<p>First</p>",
        created_at="2026-04-26T08:00:00+00:00",
        updated_at="2026-04-26T08:00:00+00:00",
        mood="calm",
    )
    second = JournalEntry(
        id="entry-2",
        title="Second",
        body="<p>Second</p>",
        created_at="2026-04-26T21:00:00+00:00",
        updated_at="2026-04-26T21:00:00+00:00",
        mood="energized",
    )
    third = JournalEntry(
        id="entry-3",
        title="Third",
        body="<p>Third</p>",
        created_at="2026-04-12T21:00:00+00:00",
        updated_at="2026-04-12T21:00:00+00:00",
        mood="reflective",
    )
    repo.save_entry(first)
    repo.save_entry(second)
    repo.save_entry(third)

    assert repo.entry_days_in_month(2026, 4) == {26: "energized", 12: "reflective"}

    repo.close()


def test_local_repo_search_handles_unicode_hyphenated_and_wildcard_only_queries(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(
        id="entry-1",
        title="Voice journal cafe",
        body="<p>I visited a café and updated my voice-journal résumé.</p>",
        tags=["mañana"],
    )
    repo.save_entry(entry)

    assert [result.id for result in repo.search_entries("café")] == [entry.id]
    assert [result.id for result in repo.search_entries("voice-journal")] == [entry.id]
    assert [result.id for result in repo.search_entries("mañana")] == [entry.id]
    assert repo.search_entries("*") == []
    assert repo.search_entries("OR") == []
    assert [result.id for result in repo.search_entries("AND")] == [entry.id]
    assert repo.search_entries("NOT") == []

    repo.close()


def test_local_repo_compresses_photo_src_for_storage_and_expands_on_load(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    photo_path = tmp_path / "photos" / "entry-1" / "pic.jpg"
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"jpg")
    entry = JournalEntry(
        id="entry-1",
        title="Photos",
        body=f'<p><img src="{photo_path}" alt="pic"></p>',
    )

    repo.save_entry(entry)

    stored_body = repo._conn.execute("SELECT body FROM entries WHERE id = ?", (entry.id,)).fetchone()[0]
    assert stored_body == '<p><img src="photos/entry-1/pic.jpg" alt="pic"></p>'
    assert repo.get_entry(entry.id).body == f'<p><img src="{photo_path}" alt="pic"></p>'

    repo.close()


def test_local_repo_compresses_unquoted_photo_src_for_storage_and_expands_on_load(tmp_path) -> None:
    data_dir = tmp_path / "Application Support"
    repo = LocalRepo(data_dir / "journal.db")
    photo_path = data_dir / "photos" / "entry-1" / "pic.jpg"
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"jpg")
    entry = JournalEntry(
        id="entry-1",
        title="Photos",
        body=f'<p><img src={photo_path} alt="pic"></p>',
    )

    repo.save_entry(entry)

    stored_body = repo._conn.execute("SELECT body FROM entries WHERE id = ?", (entry.id,)).fetchone()[0]
    assert stored_body == '<p><img src=photos/entry-1/pic.jpg alt="pic"></p>'
    assert repo.get_entry(entry.id).body == f'<p><img src="{photo_path}" alt="pic"></p>'

    repo.close()


def test_local_repo_normalizes_src_without_touching_data_src(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    photo_path = tmp_path / "photos" / "entry-1" / "pic.jpg"
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"jpg")
    entry = JournalEntry(
        id="entry-1",
        title="Lazy image",
        body=f'<p><img src="{photo_path}" data-src="https://example.com/lazy.jpg" alt="pic"></p>',
    )

    repo.save_entry(entry)

    stored_body = repo._conn.execute("SELECT body FROM entries WHERE id = ?", (entry.id,)).fetchone()[0]
    assert stored_body == '<p><img src="photos/entry-1/pic.jpg" data-src="https://example.com/lazy.jpg" alt="pic"></p>'
    assert repo.get_entry(entry.id).body == f'<p><img src="{photo_path}" data-src="https://example.com/lazy.jpg" alt="pic"></p>'

    repo.close()


def test_local_repo_escapes_quote_characters_in_normalized_body_image_paths(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    filename = 'he said "it\'s me".jpg'
    photo_path = tmp_path / "photos" / "entry-1" / filename
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"jpg")
    photo_uri = f"file://{quote(photo_path.as_posix(), safe='/:')}"
    entry = JournalEntry(
        id="entry-1",
        title="Quoted filename",
        body=f'<p><img src="{photo_uri}" alt="pic"></p>',
    )

    repo.save_entry(entry)

    stored_body = repo._conn.execute("SELECT body FROM entries WHERE id = ?", (entry.id,)).fetchone()[0]
    expected_relative = html_module.escape(f"photos/entry-1/{filename}", quote=True)
    expected_absolute = html_module.escape(str(photo_path), quote=True)
    assert stored_body == f'<p><img src="{expected_relative}" alt="pic"></p>'
    assert repo.get_entry(entry.id).body == f'<p><img src="{expected_absolute}" alt="pic"></p>'

    repo.close()


def test_local_repo_rejects_body_image_paths_for_other_entries_or_traversal(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    foreign_photo_path = tmp_path / "photos" / "entry-2" / "pic.jpg"
    unmanaged_photo_path = tmp_path / "external" / "pic.jpg"
    foreign_photo_path.parent.mkdir(parents=True)
    unmanaged_photo_path.parent.mkdir(parents=True)
    foreign_photo_path.write_bytes(b"jpg")
    unmanaged_photo_path.write_bytes(b"jpg")

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(
            JournalEntry(
                id="entry-1",
                title="Bad",
                body=f'<p><img src="{foreign_photo_path}" alt="pic"></p>',
            )
        )

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(
            JournalEntry(
                id="entry-1",
                title="Traversal",
                body='<p><img src="photos/../../etc/passwd" alt="pic"></p>',
            )
        )

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(
            JournalEntry(
                id="entry-1",
                title="Outside",
                body=f'<p><img src="{unmanaged_photo_path}" alt="pic"></p>',
            )
        )

    assert repo.get_entry("entry-1") is None

    repo.close()


def test_local_repo_rejects_missing_managed_body_image_paths(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(
            JournalEntry(
                id="entry-1",
                title="Missing",
                body='<p><img src="photos/entry-1/missing.jpg" alt="pic"></p>',
            )
        )

    assert repo.get_entry("entry-1") is None

    repo.close()


def test_local_repo_rejects_non_managed_body_image_sources(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(
            JournalEntry(
                id="entry-1",
                title="Remote",
                body='<p><img src="https://example.com/pic.jpg" alt="pic"></p>',
            )
        )

    with pytest.raises(ValueError, match="photos/entry-1/"):
        repo.save_entry(
            JournalEntry(
                id="entry-1",
                title="Remote unquoted",
                body='<p><img src=https://example.com/pic.jpg alt="pic"></p>',
            )
        )

    assert repo.get_entry("entry-1") is None

    repo.close()


def test_local_repo_does_not_expand_invalid_body_image_paths_on_read(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    repo._conn.execute(
        """
        INSERT INTO entries (
            id, created_at, updated_at, saved_at, title, body, body_text,
            body_snippet, mood, tags, voice_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "entry-1",
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:00:00+00:00",
            None,
            "Stored",
            '<p><img src="photos/entry-2/pic.jpg" alt="pic"></p>',
            "",
            "",
            None,
            "[]",
            "Lucy",
        ),
    )

    loaded = repo.get_entry("entry-1")

    assert loaded is not None
    assert loaded.body == '<p><img src="photos/entry-2/pic.jpg" alt="pic"></p>'

    repo.close()


def test_local_repo_does_not_expand_missing_managed_body_image_paths_on_read(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    repo._conn.execute(
        """
        INSERT INTO entries (
            id, created_at, updated_at, saved_at, title, body, body_text,
            body_snippet, mood, tags, voice_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "entry-1",
            "2026-04-26T00:00:00+00:00",
            "2026-04-26T00:00:00+00:00",
            None,
            "Stored",
            '<p><img src="photos/entry-1/missing.jpg" alt="pic"></p>',
            "",
            "",
            None,
            "[]",
            "Lucy",
        ),
    )

    loaded = repo.get_entry("entry-1")

    assert loaded is not None
    assert loaded.body == '<p><img src="photos/entry-1/missing.jpg" alt="pic"></p>'

    repo.close()


def test_src_to_local_path_handles_windows_file_url(monkeypatch) -> None:
    monkeypatch.setattr(local_repo_module, "Path", PureWindowsPath)

    path = local_repo_module._src_to_local_path("file:///C:/Users/jay/Pictures/pic.jpg")

    assert path == PureWindowsPath("C:/Users/jay/Pictures/pic.jpg")


def test_local_repo_round_trips_windows_file_url_body_images(tmp_path, monkeypatch) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    photo_path = tmp_path / "photos" / "entry-1" / "pic.jpg"
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"jpg")
    windows_url = "file:///C:/Users/jay/AppData/Roaming/VoiceJournal/photos/entry-1/pic.jpg"
    original_src_to_local_path = local_repo_module._src_to_local_path

    def fake_src_to_local_path(src_value: str):
        if src_value == windows_url:
            return photo_path
        return original_src_to_local_path(src_value)

    monkeypatch.setattr(local_repo_module, "_src_to_local_path", fake_src_to_local_path)

    repo.save_entry(
        JournalEntry(
            id="entry-1",
            title="Windows photo",
            body=f'<p><img src="{windows_url}" alt="pic"></p>',
        )
    )

    stored_body = repo._conn.execute("SELECT body FROM entries WHERE id = ?", ("entry-1",)).fetchone()[0]
    assert stored_body == '<p><img src="photos/entry-1/pic.jpg" alt="pic"></p>'
    assert repo.get_entry("entry-1").body == f'<p><img src="{photo_path}" alt="pic"></p>'

    repo.close()