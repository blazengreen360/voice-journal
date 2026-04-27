from __future__ import annotations

from dataclasses import dataclass

import pytest

from voicejournal.app import cli
from voicejournal.app.single_instance import AlreadyRunningError


def test_main_without_arguments_runs_application(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_run_application", lambda: 17)

    assert cli.main([]) == 17


def test_main_routes_gemma_floor_spike(monkeypatch, tmp_path) -> None:
    called: dict[str, object] = {}

    def fake_run(
        model_path: str,
        *,
        output_path: str | None = None,
        n_ctx: int = 4096,
        n_gpu_layers: int | None = None,
        verbose: bool = False,
    ) -> int:
        called.update(
            {
                "model_path": model_path,
                "output_path": output_path,
                "n_ctx": n_ctx,
                "n_gpu_layers": n_gpu_layers,
                "verbose": verbose,
            }
        )
        return 23

    report_path = tmp_path / "report.json"
    monkeypatch.setattr(cli, "run_gemma_floor_spike", fake_run)

    result = cli.main(
        [
            "spike",
            "gemma-floor",
            "--model-path",
            str(tmp_path / "gemma.gguf"),
            "--output",
            str(report_path),
            "--n-ctx",
            "8192",
            "--n-gpu-layers",
            "-1",
            "--verbose",
        ]
    )

    assert result == 23
    assert called == {
        "model_path": str(tmp_path / "gemma.gguf"),
        "output_path": str(report_path),
        "n_ctx": 8192,
        "n_gpu_layers": -1,
        "verbose": True,
    }


def test_main_ignores_non_command_arguments_for_application_launch(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_run_application", lambda: 29)

    assert cli.main(["-psn_0_12345"]) == 29


@dataclass
class _FakePaths:
    lock_file: str


class _FakeConfig:
    def __init__(self, lock_file: str) -> None:
        self._lock_file = lock_file

    def ensure_directories(self) -> _FakePaths:
        return _FakePaths(lock_file=self._lock_file)


class _FakeApp:
    def __init__(self, argv) -> None:
        self.argv = argv
        self.application_name = None
        self.organization_name = None

    def setApplicationName(self, name: str) -> None:
        self.application_name = name

    def setOrganizationName(self, name: str) -> None:
        self.organization_name = name

    def exec(self) -> int:
        return 7


class _FakeWindow:
    def __init__(self) -> None:
        self.shown = False

    def show(self) -> None:
        self.shown = True


def test_run_application_runtime_shows_specified_alert_when_already_running(monkeypatch, tmp_path) -> None:
    shown_messages: list[tuple[object, str, str]] = []
    registered_releases: list[object] = []

    class FakeMessageBox:
        @staticmethod
        def information(parent, title: str, message: str) -> None:
            shown_messages.append((parent, title, message))

    class FakeLock:
        def __init__(self, lock_file) -> None:
            self.lock_file = lock_file

        def acquire(self) -> None:
            raise AlreadyRunningError("held")

        def release(self) -> None:
            raise AssertionError("release should not be called when acquire fails")

    monkeypatch.setattr(cli.atexit, "register", lambda callback: registered_releases.append(callback))

    result = cli._run_application_runtime(
        application_factory=_FakeApp,
        message_box=FakeMessageBox,
        window_factory=_FakeWindow,
        argv=["voicejournal"],
        config_factory=lambda: _FakeConfig(str(tmp_path / "journal.db.lock")),
        lock_factory=FakeLock,
    )

    assert result == 0
    assert shown_messages == [(None, "VoiceJournal", cli.ALREADY_RUNNING_MESSAGE)]
    assert registered_releases == []


def test_run_application_runtime_releases_lock_when_window_init_fails(monkeypatch, tmp_path) -> None:
    releases: list[str] = []
    registered_releases: list[object] = []

    class FakeLock:
        def __init__(self, lock_file) -> None:
            self.lock_file = lock_file

        def acquire(self) -> None:
            return None

        def release(self) -> None:
            releases.append("released")

    class BoomWindow:
        def __init__(self) -> None:
            raise RuntimeError("boom")

    class FakeMessageBox:
        @staticmethod
        def information(parent, title: str, message: str) -> None:
            raise AssertionError("already-running alert should not be shown")

    monkeypatch.setattr(cli.atexit, "register", lambda callback: registered_releases.append(callback))

    with pytest.raises(RuntimeError, match="boom"):
        cli._run_application_runtime(
            application_factory=_FakeApp,
            message_box=FakeMessageBox,
            window_factory=BoomWindow,
            argv=["voicejournal"],
            config_factory=lambda: _FakeConfig(str(tmp_path / "journal.db.lock")),
            lock_factory=FakeLock,
        )

    assert len(registered_releases) == 1
    assert releases == ["released"]


def test_run_application_runtime_releases_lock_after_clean_exit(monkeypatch, tmp_path) -> None:
    apps: list[_FakeApp] = []
    windows: list[_FakeWindow] = []
    releases: list[str] = []
    registered_releases: list[object] = []

    class FakeLock:
        def __init__(self, lock_file) -> None:
            self.lock_file = lock_file

        def acquire(self) -> None:
            return None

        def release(self) -> None:
            releases.append("released")

    class HappyApp(_FakeApp):
        def exec(self) -> int:
            assert releases == []
            return 5

    def make_app(argv):
        app = HappyApp(argv)
        apps.append(app)
        return app

    def make_window() -> _FakeWindow:
        window = _FakeWindow()
        windows.append(window)
        return window

    class FakeMessageBox:
        @staticmethod
        def information(parent, title: str, message: str) -> None:
            raise AssertionError("already-running alert should not be shown")

    monkeypatch.setattr(cli.atexit, "register", lambda callback: registered_releases.append(callback))

    result = cli._run_application_runtime(
        application_factory=make_app,
        message_box=FakeMessageBox,
        window_factory=make_window,
        argv=["voicejournal"],
        config_factory=lambda: _FakeConfig(str(tmp_path / "journal.db.lock")),
        lock_factory=FakeLock,
    )

    assert result == 5
    assert len(apps) == 1
    assert apps[0].application_name == "VoiceJournal"
    assert apps[0].organization_name == "VoiceJournal"
    assert len(windows) == 1
    assert windows[0].shown is True
    assert len(registered_releases) == 1
    assert releases == ["released"]