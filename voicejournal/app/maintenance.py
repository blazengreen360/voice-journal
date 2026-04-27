from __future__ import annotations

from pathlib import Path
import shutil

from voicejournal.app.core.repository import JournalRepository


def reap_orphan_photo_dirs(photos_dir: Path, repo: JournalRepository) -> list[Path]:
    photos_dir.mkdir(parents=True, exist_ok=True)
    known_entry_ids = repo.all_entry_ids()
    removed_dirs: list[Path] = []

    for child in photos_dir.iterdir():
        if child.is_symlink():
            continue
        if not child.is_dir():
            continue
        if child.name in known_entry_ids:
            continue
        shutil.rmtree(child)
        removed_dirs.append(child)

    return sorted(removed_dirs)


def maintain_fts(repo: JournalRepository) -> bool:
    if repo.fts_integrity_ok():
        return False
    repo.rebuild_fts()
    return True