from __future__ import annotations

import sqlite3

from voicejournal.app.core.local_repo import LocalRepo
from voicejournal.app.maintenance import maintain_fts, reap_orphan_photo_dirs
from voicejournal.app.models.entry import JournalEntry


def test_maintain_fts_rebuilds_when_index_is_out_of_sync(tmp_path) -> None:
    db_path = tmp_path / "journal.db"
    repo = LocalRepo(db_path)
    entry = JournalEntry(id="entry-1", title="Search me", body="<p>Hello world</p>")
    repo.save_entry(entry)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM entries_fts")

    assert repo.fts_integrity_ok() is False
    assert maintain_fts(repo) is True
    assert repo.fts_integrity_ok() is True
    assert [result.id for result in repo.search_entries("hello")] == [entry.id]

    repo.close()


def test_maintain_fts_is_noop_when_index_is_healthy(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    repo.save_entry(JournalEntry(id="entry-1", title="Healthy", body="<p>Index</p>"))

    assert maintain_fts(repo) is False

    repo.close()


def test_maintain_fts_is_noop_for_healthy_unicode_index(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    repo.save_entry(JournalEntry(id="entry-1", title="Café", body="<p>mañana résumé</p>", tags=["touché"]))

    assert repo.fts_integrity_ok() is True
    assert maintain_fts(repo) is False

    repo.close()


def test_maintain_fts_rebuilds_when_index_content_drifts_for_same_rowid(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(
        id="entry-1",
        title="Search me",
        body="<p>alpha beta gamma delta epsilon zeta eta theta</p>",
    )
    repo.save_entry(entry)

    row = repo._conn.execute(
        "SELECT rowid, title, body_text, tags FROM entries WHERE id = ?",
        (entry.id,),
    ).fetchone()
    repo._conn.execute(
        """
        INSERT INTO entries_fts(entries_fts, rowid, title, body_text, tags)
        VALUES ('delete', ?, ?, ?, ?)
        """,
        (row["rowid"], row["title"], row["body_text"], row["tags"]),
    )
    repo._conn.execute(
        "INSERT INTO entries_fts(rowid, title, body_text, tags) VALUES (?, ?, ?, ?)",
        (row["rowid"], row["title"], "alpha beta gamma delta epsilon zeta changed theta", row["tags"]),
    )

    assert repo.fts_integrity_ok() is False
    assert maintain_fts(repo) is True
    assert repo.fts_integrity_ok() is True
    assert [result.id for result in repo.search_entries("eta")] == [entry.id]

    repo.close()


def test_maintain_fts_rebuilds_when_index_term_order_drifts(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(id="entry-1", title="alpha beta", body="<p>gamma delta</p>")
    repo.save_entry(entry)

    row = repo._conn.execute(
        "SELECT rowid, title, body_text, tags FROM entries WHERE id = ?",
        (entry.id,),
    ).fetchone()
    repo._conn.execute(
        """
        INSERT INTO entries_fts(entries_fts, rowid, title, body_text, tags)
        VALUES ('delete', ?, ?, ?, ?)
        """,
        (row["rowid"], row["title"], row["body_text"], row["tags"]),
    )
    repo._conn.execute(
        "INSERT INTO entries_fts(rowid, title, body_text, tags) VALUES (?, ?, ?, ?)",
        (row["rowid"], "beta alpha", row["body_text"], row["tags"]),
    )

    assert repo.fts_integrity_ok() is False
    assert repo.search_entries('"alpha beta"') == []
    assert [result.id for result in repo.search_entries('"beta alpha"')] == [entry.id]
    assert maintain_fts(repo) is True
    assert repo.fts_integrity_ok() is True
    assert [result.id for result in repo.search_entries('"alpha beta"')] == [entry.id]

    repo.close()


def test_maintain_fts_rebuilds_when_empty_entry_index_drifts(tmp_path) -> None:
    repo = LocalRepo(tmp_path / "journal.db")
    entry = JournalEntry(id="entry-1", title="", body="", tags=[])
    repo.save_entry(entry)

    row = repo._conn.execute(
        "SELECT rowid, title, body_text, tags FROM entries WHERE id = ?",
        (entry.id,),
    ).fetchone()
    repo._conn.execute(
        """
        INSERT INTO entries_fts(entries_fts, rowid, title, body_text, tags)
        VALUES ('delete', ?, ?, ?, ?)
        """,
        (row["rowid"], row["title"], row["body_text"], row["tags"]),
    )
    repo._conn.execute(
        "INSERT INTO entries_fts(rowid, title, body_text, tags) VALUES (?, ?, ?, ?)",
        (row["rowid"], "ghost", "ghost text", '["ghost"]'),
    )

    assert repo.fts_integrity_ok() is False
    assert maintain_fts(repo) is True
    assert repo.fts_integrity_ok() is True
    assert repo.search_entries("ghost") == []

    repo.close()


def test_reap_orphan_photo_dirs_removes_only_unknown_entry_dirs(tmp_path) -> None:
    photos_dir = tmp_path / "photos"
    valid_dir = photos_dir / "entry-1"
    orphan_dir = photos_dir / "entry-orphan"
    linked_target = tmp_path / "linked-target"
    linked_target.mkdir()
    symlink_dir = photos_dir / "linked-dir"
    stray_file = photos_dir / "README.txt"
    valid_dir.mkdir(parents=True)
    orphan_dir.mkdir(parents=True)
    symlink_dir.parent.mkdir(parents=True, exist_ok=True)
    symlink_dir.symlink_to(linked_target, target_is_directory=True)
    stray_file.parent.mkdir(parents=True, exist_ok=True)
    stray_file.write_text("keep me", encoding="utf-8")
    (valid_dir / "keep.jpg").write_bytes(b"ok")
    (orphan_dir / "remove.jpg").write_bytes(b"bye")

    repo = LocalRepo(tmp_path / "journal.db")
    repo.save_entry(JournalEntry(id="entry-1", title="Valid", body="<p>Body</p>"))

    removed = reap_orphan_photo_dirs(photos_dir, repo)

    assert removed == [orphan_dir]
    assert valid_dir.is_dir()
    assert orphan_dir.exists() is False
    assert symlink_dir.is_symlink()
    assert stray_file.is_file()

    repo.close()