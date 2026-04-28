from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtGui import QAction, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from voicejournal.app.core.llm import LLMEngine, LLMMessage, MetadataResult
from voicejournal.app.models.entry import ActiveSession, EntryPhoto, JournalEntry, SessionTurn
from voicejournal.app.workers.llm_worker import LLMWorker


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewScreen(QWidget):
    saved = Signal(str)

    def __init__(
        self,
        *,
        config,
        repo,
        llm_engine: LLMEngine | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._repo = repo
        self._llm_engine = llm_engine
        self._thread_pool = QThreadPool.globalInstance() if thread_pool is None else thread_pool
        self._session: ActiveSession | None = None
        self._photo_paths: list[Path] = []
        self._body_html = ""
        self._prose_streaming = False
        self._metadata_streaming = False
        self._save_intent_captured = False
        self._prose_worker: LLMWorker | None = None
        self._metadata_worker: LLMWorker | None = None
        self._generation_token = 0
        self._updating_editor = False

        self.title_label = QLabel("Review your summary", self)
        self.title_input = QLineEdit(self)
        self.title_input.setPlaceholderText("Title")
        self.mood_input = QLineEdit(self)
        self.mood_input.setPlaceholderText("Mood")
        self.tags_input = QLineEdit(self)
        self.tags_input.setPlaceholderText("Tags, separated by commas")

        fields_row = QHBoxLayout()
        fields_row.addWidget(self.title_input, 2)
        fields_row.addWidget(self.mood_input, 1)
        fields_row.addWidget(self.tags_input, 2)

        self.editor = QTextEdit(self)
        self.editor.textChanged.connect(self._on_editor_text_changed)
        self.photo_list = QListWidget(self)
        self.add_photo_button = QPushButton("Add Photo", self)
        self.add_photo_button.clicked.connect(self._pick_photos)

        photo_row = QHBoxLayout()
        photo_row.addWidget(self.add_photo_button)
        photo_row.addWidget(self.photo_list, 1)

        self.save_action = QAction("Save Entry", self)
        self.save_action.setShortcuts(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self._on_save_requested)
        self.addAction(self.save_action)

        self.save_button = QPushButton("Save Entry", self)
        self.save_button.clicked.connect(self.save_action.trigger)
        self.status_label = QLabel(self)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addLayout(fields_row)
        layout.addWidget(self.editor)
        layout.addLayout(photo_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

        self._set_save_state("idle")

    def load(
        self,
        html: str,
        metadata: MetadataResult | None,
        session: ActiveSession,
        *,
        photo_paths: Sequence[Path] = (),
    ) -> None:
        self._session = session.snapshot()
        self._photo_paths = [Path(path) for path in photo_paths]
        self._body_html = html
        self._set_editor_html(html)
        if metadata is not None:
            self.title_input.setText(metadata.title)
            self.mood_input.setText(metadata.mood or "")
            self.tags_input.setText(", ".join(metadata.tags))
        else:
            self.title_input.clear()
            self.mood_input.clear()
            self.tags_input.clear()
        self._refresh_photo_list()
        self._save_intent_captured = False
        self.status_label.clear()
        self._set_save_state("idle")

    def begin_generation(self, session: ActiveSession, photo_paths: Sequence[Path]) -> None:
        self._cancel_workers()
        self._generation_token += 1
        self._prose_streaming = False
        self._metadata_streaming = False
        self.load("", None, session, photo_paths=photo_paths)

        if self._llm_engine is None:
            fallback_metadata = MetadataResult(
                title=_default_title(session),
                mood=None,
                tags=[],
            )
            self.load(_transcript_html(session), fallback_metadata, session, photo_paths=photo_paths)
            return

        self._body_html = ""
        self._set_editor_html("")
        self.editor.setReadOnly(True)
        self._prose_streaming = False
        self._metadata_streaming = True
        self._set_save_state("streaming")
        generation = self._generation_token

        self._start_metadata_generation(session, generation)

    def _start_metadata_generation(self, session: ActiveSession, generation: int) -> None:
        if self._llm_engine is None:
            return

        metadata_worker = LLMWorker(
            engine=self._llm_engine,
            call_type="metadata",
            messages=_prompt_messages(session),
        )
        metadata_worker.signals.result.connect(
            lambda metadata, generation=generation: self._on_metadata_result(generation, metadata)
        )
        metadata_worker.signals.error.connect(
            lambda message, generation=generation: self._on_metadata_error(generation, message)
        )
        metadata_worker.signals.done.connect(lambda generation=generation: self._on_metadata_done(generation))
        self._metadata_worker = metadata_worker
        self._thread_pool.start(metadata_worker)

    def _start_prose_generation(self, generation: int) -> None:
        if self._llm_engine is None or self._session is None:
            return

        self._prose_streaming = True
        self.status_label.setText("Summarizing your entry…")
        prose_worker = LLMWorker(
            engine=self._llm_engine,
            call_type="prose",
            messages=_summary_prompt_messages(self._session),
        )
        prose_worker.signals.chunk.connect(
            lambda chunk, generation=generation: self._on_prose_chunk(generation, chunk)
        )
        prose_worker.signals.result.connect(
            lambda html, generation=generation: self._on_prose_result(generation, html)
        )
        prose_worker.signals.error.connect(
            lambda message, generation=generation: self._on_prose_error(generation, message)
        )
        prose_worker.signals.done.connect(lambda generation=generation: self._on_prose_done(generation))
        self._prose_worker = prose_worker
        self._thread_pool.start(prose_worker)

    def _on_prose_chunk(self, generation: int, chunk: str) -> None:
        if generation != self._generation_token:
            return
        self._body_html += chunk
        self._set_editor_html(self._body_html)
        self.status_label.setText("Summarizing your entry…")

    def _on_prose_result(self, generation: int, html: str) -> None:
        if generation != self._generation_token:
            return
        self._body_html = html or _transcript_html(self._session)
        self._set_editor_html(self._body_html)

    def _on_prose_error(self, generation: int, _message: str) -> None:
        if generation != self._generation_token:
            return
        if self._session is None:
            return
        self._body_html = _transcript_html(self._session)
        self._set_editor_html(self._body_html)

    def _on_prose_done(self, generation: int) -> None:
        if generation != self._generation_token:
            return
        self._prose_streaming = False
        self._prose_worker = None
        self._maybe_finish_generation()

    def _on_metadata_result(self, generation: int, metadata: MetadataResult) -> None:
        if generation != self._generation_token:
            return
        self.title_input.setText(metadata.title)
        self.mood_input.setText(metadata.mood or "")
        self.tags_input.setText(", ".join(metadata.tags))

    def _on_metadata_error(self, generation: int, _message: str) -> None:
        if generation != self._generation_token:
            return
        if self._session is None:
            return
        self.title_input.setText(_default_title(self._session))

    def _on_metadata_done(self, generation: int) -> None:
        if generation != self._generation_token:
            return
        self._metadata_streaming = False
        self._metadata_worker = None
        self._start_prose_generation(generation)
        if self._prose_streaming:
            return
        self._maybe_finish_generation()

    def _maybe_finish_generation(self) -> None:
        if self._prose_streaming or self._metadata_streaming:
            return

        self.editor.setReadOnly(False)
        if not self.title_input.text().strip() and self._session is not None:
            self.title_input.setText(_default_title(self._session))
        if self.status_label.text() == "Summarizing your entry…":
            self.status_label.clear()

        if self._save_intent_captured:
            self._perform_save()
        else:
            self._set_save_state("idle")

    def _on_save_requested(self) -> None:
        if self._session is None:
            return

        if self._prose_streaming or self._metadata_streaming:
            self._save_intent_captured = True
            self._set_save_state("intent")
            return

        self._perform_save()

    def _perform_save(self) -> None:
        if self._session is None:
            return

        self._set_save_state("saving")
        saved_at = _now()
        imported_paths: list[str] = []
        try:
            entry = JournalEntry(
                id=self._session.entry_id,
                title=self.title_input.text().strip() or _default_title(self._session),
                body=self._body_html or _extract_body_html(self.editor.toHtml()),
                updated_at=saved_at,
                saved_at=saved_at,
                mood=self.mood_input.text().strip() or None,
                tags=_parse_tags(self.tags_input.text()),
                voice_name=self._session.voice_name,
            )
            turns = [
                SessionTurn(
                    entry_id=entry.id,
                    turn_index=index,
                    role=turn["role"],
                    text=turn["text"],
                )
                for index, turn in enumerate(self._session.turns)
            ]
            photos = []
            for sort_order, photo_path in enumerate(self._photo_paths):
                relative_path = self._repo.import_photo_file(photo_path, self._config.paths().photos_dir, entry.id)
                imported_paths.append(relative_path)
                photos.append(
                    EntryPhoto(
                        entry_id=entry.id,
                        file_path=relative_path,
                        sort_order=sort_order,
                    )
                )

            self._repo.save_entry(entry, turns=turns, photos=photos)
        except Exception as exc:
            self._cleanup_imported_photos(imported_paths)
            self.status_label.setText(f"Could not save entry. {exc}")
            self._set_save_state("idle")
            return

        self.status_label.setText("Saved.")
        self.saved.emit(entry.id)
        self._set_save_state("idle")

    def _set_save_state(self, state: str) -> None:
        if state == "streaming":
            label = "Save when ready"
            enabled = True
        elif state == "intent":
            label = "Saving when ready…"
            enabled = False
        elif state == "saving":
            label = "Saving…"
            enabled = False
        else:
            label = "Save Entry"
            enabled = True

        self.save_button.setText(label)
        self.save_action.setEnabled(enabled)
        self.save_button.setEnabled(enabled)

    def _pick_photos(self) -> None:
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Photo",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        if file_paths:
            self.add_photo_paths([Path(path) for path in file_paths])

    def set_llm_engine(self, llm_engine: LLMEngine | None) -> None:
        self._llm_engine = llm_engine

    def add_photo_paths(self, photo_paths: Sequence[Path]) -> None:
        for photo_path in photo_paths:
            path_obj = Path(photo_path)
            if path_obj not in self._photo_paths:
                self._photo_paths.append(path_obj)
        self._refresh_photo_list()

    def _refresh_photo_list(self) -> None:
        self.photo_list.clear()
        for path in self._photo_paths:
            self.photo_list.addItem(path.name)

    def _cancel_workers(self) -> None:
        if self._prose_worker is not None:
            self._prose_worker.cancel()
            self._prose_worker = None
        if self._metadata_worker is not None:
            self._metadata_worker.cancel()
            self._metadata_worker = None

    def _cleanup_imported_photos(self, relative_paths: Sequence[str]) -> None:
        data_dir = self._config.paths().data_dir
        for relative_path in relative_paths:
            candidate = data_dir / relative_path
            if candidate.is_file():
                try:
                    candidate.unlink()
                except OSError:
                    continue

    def _set_editor_html(self, html: str) -> None:
        self._updating_editor = True
        try:
            self.editor.setHtml(html)
        finally:
            self._updating_editor = False

    def _on_editor_text_changed(self) -> None:
        if self._updating_editor or self._prose_streaming:
            return
        self._body_html = _extract_body_html(self.editor.toHtml())


def _prompt_messages(session: ActiveSession) -> list[LLMMessage]:
    return [{"role": turn["role"], "content": turn["text"]} for turn in session.turns]


def _summary_prompt_messages(session: ActiveSession) -> list[LLMMessage]:
    lines = []
    for turn in session.turns:
        speaker = "You" if turn["role"] == "user" else "VoiceJournal"
        text = turn["text"].strip()
        if text:
            lines.append(f"{speaker}: {text}")
    transcript = "\n".join(lines).strip() or "No journal details were captured."
    return [
        {
            "role": "user",
            "content": (
                "Summarize this journaling conversation into a polished first-person "
                "journal entry. Keep the user's perspective, preserve concrete details, "
                "and do not review or critique the writing.\n\n"
                f"Conversation:\n{transcript}"
            ),
        }
    ]


def _default_title(session: ActiveSession | None) -> str:
    if session is None:
        return "Untitled entry"
    for turn in session.turns:
        if turn["role"] == "user" and turn["text"].strip():
            return turn["text"].strip()[:48]
    return "Untitled entry"


def _parse_tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.split(",") if tag.strip()]


def _transcript_html(session: ActiveSession | None) -> str:
    if session is None or not session.turns:
        return "<p></p>"
    parts = [
        f"<p><strong>{escape('You' if turn['role'] == 'user' else 'VoiceJournal')}</strong> · {escape(turn['text'])}</p>"
        for turn in session.turns
    ]
    return "".join(parts)


def _extract_body_html(document_html: str) -> str:
    lower = document_html.lower()
    start = lower.find("<body")
    if start < 0:
        return document_html
    start = document_html.find(">", start)
    if start < 0:
        return document_html
    end = lower.rfind("</body>")
    if end < 0:
        return document_html[start + 1 :]
    return document_html[start + 1 : end].strip()


__all__ = ["ReviewScreen"]