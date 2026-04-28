from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from voicejournal.app.config import AppPaths
from voicejournal.app.core.local_repo import LocalRepo
from voicejournal.app.core.llm import MetadataResult
from voicejournal.app.models.entry import ActiveSession
from voicejournal.app.ui.review_screen import ReviewScreen


@dataclass(frozen=True, slots=True)
class _FakeConfig:
    _paths: AppPaths

    def paths(self) -> AppPaths:
        return self._paths


class _FakeLLM:
    def stream_prose(self, _messages):
        yield "<p>Hello"
        yield " world</p>"

    def stream_metadata(self, _messages):
        yield '{"title":"A quiet evening",'
        yield '"mood":"Reflective",'
        yield '"tags":["home","quiet"]}'

    def parse_metadata(self, raw_text: str):
        assert "A quiet evening" in raw_text
        return MetadataResult(title="A quiet evening", mood="Reflective", tags=["home", "quiet"])


class _FailingRepo:
    def import_photo_file(self, *_args, **_kwargs):
        raise AssertionError("import_photo_file should not be called in this test")

    def save_entry(self, *_args, **_kwargs):
        raise RuntimeError("disk full")


class _RollbackRepo:
    def __init__(self, config: _FakeConfig) -> None:
        self._config = config

    def import_photo_file(self, source_path: Path, _photos_dir: Path, entry_id: str) -> str:
        destination = self._config.paths().photos_dir / entry_id / source_path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_path.read_bytes())
        return str(destination.relative_to(self._config.paths().data_dir).as_posix())

    def save_entry(self, *_args, **_kwargs):
        raise RuntimeError("disk full")


class _MetadataErrorLLM(_FakeLLM):
    def parse_metadata(self, _raw_text: str):
        raise ValueError("bad metadata")


class _RecordingLLM(_FakeLLM):
    def __init__(self) -> None:
        self.prose_messages = []

    def stream_prose(self, messages):
        self.prose_messages.append(list(messages))
        yield "<p>I made tea and sat down after a quiet walk home.</p>"


def _wait_until(app: QApplication, predicate, timeout_ms: int = 4000) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
    assert predicate()


def _make_config(tmp_path: Path) -> _FakeConfig:
    paths = AppPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        logs_dir=tmp_path / "data" / "logs",
        models_dir=tmp_path / "models",
        photos_dir=tmp_path / "data" / "photos",
    )
    for path in (
        paths.config_dir,
        paths.data_dir,
        paths.cache_dir,
        paths.logs_dir,
        paths.models_dir,
        paths.photos_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return _FakeConfig(paths)


def test_review_screen_save_when_ready_auto_saves_after_streaming(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    screen = ReviewScreen(
        config=config,
        repo=repo,
        llm_engine=_FakeLLM(),
        thread_pool=QThreadPool(),
    )
    photo_path = tmp_path / "source.jpg"
    photo_path.write_bytes(b"jpg")
    session = ActiveSession(entry_id="entry-1", voice_name="Lucy")
    session.add_turn("user", "It felt quiet when I got home.")
    session.add_turn("assistant", "What happened next?")
    session.add_turn("user", "I made tea and sat down.")
    saved: list[str] = []
    screen.saved.connect(saved.append)

    try:
        screen.begin_generation(session, [photo_path])
        screen.save_action.trigger()

        _wait_until(app, lambda: saved == ["entry-1"])

        entry = repo.get_entry("entry-1")
        assert entry is not None
        assert entry.title == "A quiet evening"
        assert "<html" not in entry.body.lower()
        assert repo.list_session_turns("entry-1")
        assert len(repo.list_entry_photos("entry-1")) == 1
    finally:
        screen.close()
        repo.close()
        app.processEvents()


def test_review_screen_requests_summary_not_review(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    llm = _RecordingLLM()
    screen = ReviewScreen(
        config=config,
        repo=repo,
        llm_engine=llm,
        thread_pool=QThreadPool(),
    )
    session = ActiveSession(entry_id="entry-summary", voice_name="Lucy")
    session.add_turn("user", "I walked home quietly.")
    session.add_turn("assistant", "What happened next?")
    session.add_turn("user", "I made tea and finally relaxed.")

    try:
        screen.begin_generation(session, [])

        _wait_until(app, lambda: screen.save_button.text() == "Save Entry")

        assert screen.title_label.text() == "Review your summary"
        assert llm.prose_messages
        summary_prompt = llm.prose_messages[0][0]["content"]
        assert "Summarize this journaling conversation" in summary_prompt
        assert "do not review or critique" in summary_prompt
        assert "You: I walked home quietly." in summary_prompt
        assert "You: I made tea and finally relaxed." in summary_prompt
        assert "I made tea and sat down" in screen.editor.toPlainText()
    finally:
        screen.close()
        repo.close()
        app.processEvents()


def test_review_screen_ignores_stale_generation_done_signals(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    screen = ReviewScreen(
        config=config,
        repo=repo,
        llm_engine=_FakeLLM(),
        thread_pool=QThreadPool(),
    )
    first = ActiveSession(entry_id="entry-1", voice_name="Lucy")
    second = ActiveSession(entry_id="entry-2", voice_name="Lucy")
    first.add_turn("user", "First")
    second.add_turn("user", "Second")

    try:
        screen.begin_generation(first, [])
        stale_generation = screen._generation_token
        screen.begin_generation(second, [])

        screen._on_prose_done(stale_generation)
        screen._on_metadata_done(stale_generation)

        assert screen.save_button.text() == "Save when ready"
        assert screen._generation_token != stale_generation
    finally:
        screen.close()
        repo.close()
        app.processEvents()


def test_review_screen_without_llm_allows_immediate_save(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    screen = ReviewScreen(
        config=config,
        repo=repo,
        llm_engine=None,
        thread_pool=QThreadPool(),
    )
    session = ActiveSession(entry_id="entry-2", voice_name="Lucy")
    session.add_turn("user", "A calm walk home.")
    saved: list[str] = []
    screen.saved.connect(saved.append)

    try:
        screen.begin_generation(session, [])
        screen.save_action.trigger()

        _wait_until(app, lambda: saved == ["entry-2"])

        entry = repo.get_entry("entry-2")
        assert entry is not None
        assert entry.title == "A calm walk home."
    finally:
        screen.close()
        repo.close()
        app.processEvents()


def test_review_screen_save_failure_recovers_button_state(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    screen = ReviewScreen(
        config=config,
        repo=_FailingRepo(),
        llm_engine=None,
        thread_pool=QThreadPool(),
    )
    session = ActiveSession(entry_id="entry-3", voice_name="Lucy")
    session.add_turn("user", "A difficult day.")

    try:
        screen.begin_generation(session, [])
        screen.save_action.trigger()

        assert screen.save_button.text() == "Save Entry"
        assert screen.save_button.isEnabled() is True
        assert "Could not save entry." in screen.status_label.text()
    finally:
        screen.close()
        app.processEvents()


def test_review_screen_save_failure_cleans_up_imported_photos(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = _RollbackRepo(config)
    screen = ReviewScreen(
        config=config,
        repo=repo,
        llm_engine=None,
        thread_pool=QThreadPool(),
    )
    photo_path = tmp_path / "source.jpg"
    photo_path.write_bytes(b"jpg")
    session = ActiveSession(entry_id="entry-4", voice_name="Lucy")
    session.add_turn("user", "A difficult day.")

    try:
        screen.begin_generation(session, [photo_path])
        screen.save_action.trigger()

        copied = config.paths().photos_dir / "entry-4" / "source.jpg"
        assert copied.exists() is False
        assert screen.save_button.isEnabled() is True
    finally:
        screen.close()
        app.processEvents()


def test_review_screen_metadata_error_falls_back_to_default_title(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = _make_config(tmp_path)
    repo = LocalRepo(config.paths().database_file)
    screen = ReviewScreen(
        config=config,
        repo=repo,
        llm_engine=_MetadataErrorLLM(),
        thread_pool=QThreadPool(),
    )
    session = ActiveSession(entry_id="entry-5", voice_name="Lucy")
    session.add_turn("user", "A calm walk home.")

    try:
        screen.begin_generation(session, [])

        _wait_until(app, lambda: screen.save_button.text() == "Save Entry")

        assert screen.title_input.text() == "A calm walk home."
    finally:
        screen.close()
        repo.close()
        app.processEvents()