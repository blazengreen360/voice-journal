from __future__ import annotations

from contextlib import contextmanager
import html as html_module
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import textwrap
from typing import Iterator, Sequence
import unicodedata
from urllib.parse import unquote, urlparse

from voicejournal.app.core.repository import JournalRepository
from voicejournal.app.models.entry import EntryPhoto, JournalEntry, SessionTurn


ENTRY_SNIPPET_WIDTH = 160
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "dl",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tr",
    "ul",
}
_FTS_TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')
_FTS_TERM_RE = re.compile(r"\w+\*?", flags=re.UNICODE)
_IMG_SRC_RE = re.compile(
    r'''(<img\b[^>]*?)(\ssrc\s*=\s*)(?:"([^"]+)"|'([^']+)'|(.+?)(?=(?:\s+[A-Za-z_:][-A-Za-z0-9_:.]*\s*=)|\s*/?>|/?>))''',
    flags=re.IGNORECASE,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    saved_at     TEXT,
    title        TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    body_text    TEXT NOT NULL DEFAULT '',
    body_snippet TEXT NOT NULL DEFAULT '',
    mood         TEXT,
    tags         TEXT NOT NULL DEFAULT '[]',
    voice_name   TEXT NOT NULL DEFAULT 'Lucy'
);
CREATE INDEX IF NOT EXISTS entries_created_idx ON entries(created_at);

CREATE TABLE IF NOT EXISTS session_turns (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    turn_index  INTEGER NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_photos (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    caption     TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title,
    body_text,
    tags,
    content=entries,
    content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, title, body_text, tags)
    VALUES (new.rowid, new.title, new.body_text, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, body_text, tags)
    VALUES ('delete', old.rowid, old.title, old.body_text, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, body_text, tags)
    VALUES ('delete', old.rowid, old.title, old.body_text, old.tags);
    INSERT INTO entries_fts(rowid, title, body_text, tags)
    VALUES (new.rowid, new.title, new.body_text, new.tags);
END;
"""


def _open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.row_factory = sqlite3.Row
    return conn


class LocalRepo(JournalRepository):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._data_dir = db_path.parent
        self._conn = _open_connection(db_path)
        self._conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self._conn.close()

    def save_entry(
        self,
        entry: JournalEntry,
        *,
        turns: Sequence[SessionTurn] | None = None,
        photos: Sequence[EntryPhoto] | None = None,
    ) -> None:
        stored_body = _compress_photo_src(entry.body, self._data_dir, entry.id)
        body_text = _html_to_text(stored_body)
        body_snippet = _build_body_snippet(body_text)
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        _validate_child_ownership(entry.id, turns, photos, data_dir=self._data_dir)

        with _transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO entries (
                    id, created_at, updated_at, saved_at, title, body, body_text,
                    body_snippet, mood, tags, voice_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    saved_at=excluded.saved_at,
                    title=excluded.title,
                    body=excluded.body,
                    body_text=excluded.body_text,
                    body_snippet=excluded.body_snippet,
                    mood=excluded.mood,
                    tags=excluded.tags,
                    voice_name=excluded.voice_name
                """,
                (
                    entry.id,
                    entry.created_at,
                    entry.updated_at,
                    entry.saved_at,
                    entry.title,
                    stored_body,
                    body_text,
                    body_snippet,
                    entry.mood,
                    tags_json,
                    entry.voice_name,
                ),
            )
            if turns is not None:
                self._conn.execute("DELETE FROM session_turns WHERE entry_id = ?", (entry.id,))
                self._conn.executemany(
                    """
                    INSERT INTO session_turns (id, entry_id, turn_index, role, text, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            turn.id,
                            turn.entry_id,
                            turn.turn_index,
                            turn.role,
                            turn.text,
                            turn.created_at,
                        )
                        for turn in turns
                    ],
                )
            if photos is not None:
                self._conn.execute("DELETE FROM entry_photos WHERE entry_id = ?", (entry.id,))
                self._conn.executemany(
                    """
                    INSERT INTO entry_photos (id, entry_id, file_path, caption, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            photo.id,
                            photo.entry_id,
                            photo.file_path,
                            photo.caption,
                            photo.sort_order,
                            photo.created_at,
                        )
                        for photo in photos
                    ],
                )

    def get_entry(self, entry_id: str) -> JournalEntry | None:
        row = self._conn.execute(
            """
            SELECT id, created_at, updated_at, saved_at, title, body, mood, tags, voice_name
            FROM entries WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
        return _entry_from_row(row, data_dir=self._data_dir) if row is not None else None

    def list_session_turns(self, entry_id: str) -> list[SessionTurn]:
        rows = self._conn.execute(
            """
            SELECT id, entry_id, turn_index, role, text, created_at
            FROM session_turns
            WHERE entry_id = ?
            ORDER BY turn_index ASC, created_at ASC
            """,
            (entry_id,),
        ).fetchall()
        return [SessionTurn(**dict(row)) for row in rows]

    def list_entry_photos(self, entry_id: str) -> list[EntryPhoto]:
        rows = self._conn.execute(
            """
            SELECT id, entry_id, file_path, caption, sort_order, created_at
            FROM entry_photos
            WHERE entry_id = ?
            ORDER BY sort_order ASC, created_at ASC
            """,
            (entry_id,),
        ).fetchall()
        return [EntryPhoto(**dict(row)) for row in rows]

    def delete_entry(self, entry_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0

    def search_entries(self, query: str, *, limit: int = 50) -> list[JournalEntry]:
        if not query.strip():
            rows = self._conn.execute(
                """
                SELECT id, created_at, updated_at, saved_at, title, body, mood, tags, voice_name
                FROM entries
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [_entry_from_row(row, data_dir=self._data_dir) for row in rows]

        sanitized_query = _sanitize_fts_query(query)
        if not sanitized_query:
            return []

        rows = self._conn.execute(
            """
            SELECT e.id, e.created_at, e.updated_at, e.saved_at, e.title, e.body, e.mood, e.tags, e.voice_name
            FROM entries_fts
            JOIN entries AS e ON e.rowid = entries_fts.rowid
            WHERE entries_fts MATCH ?
            ORDER BY bm25(entries_fts), e.updated_at DESC, e.created_at DESC
            LIMIT ?
            """,
            (sanitized_query, limit),
        ).fetchall()
        return [_entry_from_row(row, data_dir=self._data_dir) for row in rows]

    def all_entry_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT id FROM entries").fetchall()
        return {str(row["id"]) for row in rows}

    def update_photo_orders(self, entry_id: str, photo_ids: Sequence[str]) -> None:
        current_photo_ids = [photo.id for photo in self.list_entry_photos(entry_id)]
        if len(set(photo_ids)) != len(photo_ids):
            raise ValueError("Photo ordering contains duplicate ids")
        if set(photo_ids) != set(current_photo_ids) or len(photo_ids) != len(current_photo_ids):
            raise ValueError("Photo ordering must include each persisted photo exactly once")

        with _transaction(self._conn):
            for sort_order, photo_id in enumerate(photo_ids):
                cursor = self._conn.execute(
                    """
                    UPDATE entry_photos
                    SET sort_order = ?
                    WHERE entry_id = ? AND id = ?
                    """,
                    (sort_order, entry_id, photo_id),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Photo '{photo_id}' not found for entry '{entry_id}'")

    def entry_days_in_month(self, year: int, month: int) -> dict[int, str | None]:
        start = f"{year:04d}-{month:02d}-01"
        end_year = year + (1 if month == 12 else 0)
        end_month = 1 if month == 12 else month + 1
        end = f"{end_year:04d}-{end_month:02d}-01"
        rows = self._conn.execute(
            """
            SELECT CAST(strftime('%d', created_at) AS INTEGER) AS day, mood
            FROM entries
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at DESC
            """,
            (start, end),
        ).fetchall()

        day_map: dict[int, str | None] = {}
        for row in rows:
            day = int(row["day"])
            if day not in day_map:
                day_map[day] = row["mood"]
        return day_map

    def import_photo_file(self, source_path: Path, photos_dir: Path, entry_id: str) -> str:
        if not source_path.is_file():
            raise FileNotFoundError(f"Photo source not found: {source_path}")

        entry_dir = photos_dir / entry_id
        entry_dir.mkdir(parents=True, exist_ok=True)

        candidate_path = _unique_photo_path(entry_dir, source_path.name)
        shutil.copy2(source_path, candidate_path)
        return str(candidate_path.relative_to(photos_dir.parent).as_posix())

    def fts_integrity_ok(self) -> bool:
        try:
            entry_rowids = {
                int(row["rowid"])
                for row in self._conn.execute("SELECT rowid FROM entries").fetchall()
            }
            indexed_rowids = {
                int(row["docid"])
                for row in self._conn.execute(
                    "SELECT rowid AS docid FROM entries_fts_docsize"
                ).fetchall()
            }
            if entry_rowids != indexed_rowids:
                return False
            indexed_terms_by_doc = _load_indexed_terms_by_doc(self._conn)
            for row in self._conn.execute(
                "SELECT rowid, title, body_text, tags FROM entries ORDER BY rowid ASC"
            ).fetchall():
                docid = int(row["rowid"])
                expected_terms = {
                    "title": _normalized_fts_terms(str(row["title"])),
                    "body_text": _normalized_fts_terms(str(row["body_text"])),
                    "tags": _normalized_fts_terms(str(row["tags"])),
                }
                actual_terms = indexed_terms_by_doc.get(
                    docid,
                    {"title": [], "body_text": [], "tags": []},
                )
                if expected_terms != actual_terms:
                    return False
            self._conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('integrity-check')")
        except sqlite3.DatabaseError:
            return False
        return True

    def rebuild_fts(self) -> None:
        self._conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._append_newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)

    def _append_newline(self) -> None:
        if self._parts and self._parts[-1].endswith("\n"):
            return
        self._parts.append("\n")


def _html_to_text(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    text = stripper.text()
    return "\n".join(part.strip() for part in text.splitlines() if part.strip())


def _build_body_snippet(body_text: str) -> str:
    if not body_text:
        return ""
    return textwrap.shorten(" ".join(body_text.split()), width=ENTRY_SNIPPET_WIDTH, placeholder="...")


def _sanitize_fts_query(query: str) -> str:
    parts: list[str] = []
    for phrase, bare in _FTS_TOKEN_RE.findall(query):
        if phrase:
            cleaned_phrase = " ".join(_extract_fts_terms(phrase))
            if cleaned_phrase:
                parts.append(f'"{cleaned_phrase}"')
            continue

        parts.extend(_literalize_reserved_term(term) for term in _extract_fts_terms(bare))
    return " ".join(parts)


def _extract_fts_terms(text: str) -> list[str]:
    return _FTS_TERM_RE.findall(text)


def _normalized_fts_terms(text: str) -> list[str]:
    normalized_terms: list[str] = []
    for term in _extract_fts_terms(text):
        folded = unicodedata.normalize("NFKD", term.casefold())
        normalized_terms.append("".join(char for char in folded if not unicodedata.combining(char)))
    return normalized_terms


def _literalize_reserved_term(term: str) -> str:
    if term.casefold() in {"and", "or", "not"}:
        return f'"{term}"'
    return term


def _load_indexed_terms_by_doc(
    conn: sqlite3.Connection,
) -> dict[int, dict[str, list[str]]]:
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts_vocab USING fts5vocab(entries_fts, instance)")
    indexed_terms: dict[int, dict[str, list[str]]] = {}
    for row in conn.execute(
        "SELECT term, doc, col, offset FROM entries_fts_vocab ORDER BY doc, col, offset"
    ).fetchall():
        docid = int(row["doc"])
        column = str(row["col"])
        term = str(row["term"])
        by_column = indexed_terms.setdefault(
            docid,
            {"title": [], "body_text": [], "tags": []},
        )
        by_column.setdefault(column, []).append(term)
    return indexed_terms


def _validate_child_ownership(
    entry_id: str,
    turns: Sequence[SessionTurn] | None,
    photos: Sequence[EntryPhoto] | None,
    *,
    data_dir: Path,
) -> None:
    if turns is not None:
        for turn in turns:
            if turn.entry_id != entry_id:
                raise ValueError(f"Session turn '{turn.id}' belongs to '{turn.entry_id}', expected '{entry_id}'")
    if photos is not None:
        for photo in photos:
            if photo.entry_id != entry_id:
                raise ValueError(f"Photo '{photo.id}' belongs to '{photo.entry_id}', expected '{entry_id}'")
            if _managed_photo_file(data_dir, photo.file_path, entry_id) is None:
                raise ValueError(
                    f"Photo '{photo.id}' must use an existing relative path under photos/{entry_id}/"
                )


def _photo_path_is_valid(file_path: str, entry_id: str) -> bool:
    if not file_path or "\\" in file_path:
        return False
    path = PurePosixPath(file_path)
    parts = path.parts
    if path.is_absolute() or len(parts) != 3:
        return False
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return parts[0] == "photos" and parts[1] == entry_id


def _managed_photo_file(data_dir: Path, file_path: str, entry_id: str) -> Path | None:
    if not _photo_path_is_valid(file_path, entry_id):
        return None
    data_root = data_dir.resolve(strict=False)
    candidate = (data_root / PurePosixPath(file_path)).resolve(strict=False)
    try:
        candidate.relative_to(data_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _img_src_parts(match: re.Match[str]) -> tuple[str, str, str]:
    prefix = f"{match.group(1)}{match.group(2)}"
    if match.group(3) is not None:
        return prefix, '"', match.group(3)
    if match.group(4) is not None:
        return prefix, "'", match.group(4)
    return prefix, "", match.group(5)


def _img_src_quote(src_value: str, preferred_quote: str) -> str:
    if preferred_quote:
        return preferred_quote
    if any(char.isspace() for char in src_value) or any(char in src_value for char in '"\'=<>`'):
        return "'" if '"' in src_value and "'" not in src_value else '"'
    return ""


def _compress_photo_src(body: str, data_dir: Path, entry_id: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        prefix, original_quote, src_value = _img_src_parts(match)
        src_value = html_module.unescape(src_value)
        if src_value.startswith("photos/"):
            if _managed_photo_file(data_dir, src_value, entry_id) is None:
                raise ValueError(f"Body image paths must reference existing files under photos/{entry_id}/")
            return match.group(0)
        path = _src_to_local_path(src_value)
        if path is None:
            raise ValueError(f"Body image paths must stay under photos/{entry_id}/")
        try:
            relative_path = path.resolve(strict=False).relative_to(data_dir.resolve(strict=False))
        except ValueError:
            raise ValueError(f"Body image paths must stay under photos/{entry_id}/")
        relative_posix = PurePosixPath(relative_path.as_posix())
        if _managed_photo_file(data_dir, relative_posix.as_posix(), entry_id) is None:
            raise ValueError(f"Body image paths must reference existing files under photos/{entry_id}/")
        quote = _img_src_quote(relative_posix.as_posix(), original_quote)
        escaped_src = html_module.escape(relative_posix.as_posix(), quote=True)
        return f"{prefix}{quote}{escaped_src}{quote}"

    return _IMG_SRC_RE.sub(replacer, body)


def _expand_photo_src(body: str, data_dir: Path, entry_id: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        prefix, original_quote, src_value = _img_src_parts(match)
        src_value = html_module.unescape(src_value)
        managed_path = _managed_photo_file(data_dir, src_value, entry_id)
        if managed_path is None:
            return match.group(0)
        quote = _img_src_quote(str(managed_path), original_quote)
        escaped_src = html_module.escape(str(managed_path), quote=True)
        return f"{prefix}{quote}{escaped_src}{quote}"

    return _IMG_SRC_RE.sub(replacer, body)


def _src_to_local_path(src_value: str) -> Path | None:
    parsed = urlparse(src_value)
    if parsed.scheme in {"", "file"}:
        candidate = unquote(parsed.path if parsed.scheme == "file" else src_value)
        if re.match(r"^/[A-Za-z]:/", candidate):
            candidate = candidate[1:]
        if parsed.netloc and parsed.netloc != "localhost":
            candidate = f"//{parsed.netloc}{candidate}"
        path = Path(candidate)
        if path.is_absolute():
            return path
    return None


def _entry_from_row(row: sqlite3.Row, *, data_dir: Path) -> JournalEntry:
    tags_value = row["tags"]
    tags = json.loads(tags_value) if tags_value else []
    return JournalEntry(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        saved_at=row["saved_at"],
        title=row["title"],
        body=_expand_photo_src(str(row["body"]), data_dir, str(row["id"])),
        mood=row["mood"],
        tags=list(tags),
        voice_name=row["voice_name"],
    )


def _unique_photo_path(entry_dir: Path, original_name: str) -> Path:
    candidate = entry_dir / original_name
    if not candidate.exists():
        return candidate

    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    index = 2
    while True:
        candidate = entry_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1