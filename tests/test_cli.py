from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from voicejournal.app.config import AppPaths

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
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    logs_dir: Path
    models_dir: Path
    photos_dir: Path
    database_file: Path
    lock_file: Path


class _FakeConfig:
    def __init__(self, root: Path) -> None:
        self._paths = AppPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            logs_dir=root / "data" / "logs",
            models_dir=root / "data" / "models",
            photos_dir=root / "data" / "photos",
        )

    def ensure_directories(self) -> _FakePaths:
        for path in (
            self._paths.config_dir,
            self._paths.data_dir,
            self._paths.cache_dir,
            self._paths.logs_dir,
            self._paths.models_dir,
            self._paths.photos_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return _FakePaths(
            config_dir=self._paths.config_dir,
            data_dir=self._paths.data_dir,
            cache_dir=self._paths.cache_dir,
            logs_dir=self._paths.logs_dir,
            models_dir=self._paths.models_dir,
            photos_dir=self._paths.photos_dir,
            database_file=self._paths.database_file,
            lock_file=self._paths.lock_file,
        )


class _FakeApp:
    def __init__(self, argv) -> None:
        self.argv = argv
        self.application_name = None
        self.organization_name = None
        self.style_sheet = None
        self.event_filters: list[object] = []

    def setApplicationName(self, name: str) -> None:
        self.application_name = name

    def setOrganizationName(self, name: str) -> None:
        self.organization_name = name

    def setStyleSheet(self, style_sheet: str) -> None:
        self.style_sheet = style_sheet

    def installEventFilter(self, event_filter: object) -> None:
        self.event_filters.append(event_filter)

    def exec(self) -> int:
        return 7


class _FakeWindow:
    def __init__(self, *, config=None, repo=None, registry=None) -> None:
        self.shown = False
        self.config = config
        self.repo = repo
        self.registry = registry

    def show(self) -> None:
        self.shown = True


def test_run_application_runtime_shows_specified_alert_when_already_running(monkeypatch, tmp_path) -> None:
    shown_messages: list[tuple[object, str, str]] = []
    registered_releases: list[object] = []
    theme_calls: list[str] = []

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
        config_factory=lambda: _FakeConfig(tmp_path),
        lock_factory=FakeLock,
        theme_installer=lambda app, _config: theme_calls.append("theme"),
    )

    assert result == 0
    assert shown_messages == [(None, "VoiceJournal", cli.ALREADY_RUNNING_MESSAGE)]
    assert registered_releases == []
    assert theme_calls == ["theme"]


def test_run_application_runtime_releases_lock_when_window_init_fails(monkeypatch, tmp_path) -> None:
    releases: list[str] = []
    registered_releases: list[object] = []
    repo_closes: list[str] = []

    class FakeLock:
        def __init__(self, lock_file) -> None:
            self.lock_file = lock_file

        def acquire(self) -> None:
            return None

        def release(self) -> None:
            releases.append("released")

    class BoomWindow:
        def __init__(self, *, config=None, repo=None, registry=None) -> None:
            raise RuntimeError("boom")

    class FakeMessageBox:
        @staticmethod
        def information(parent, title: str, message: str) -> None:
            raise AssertionError("already-running alert should not be shown")

    class FakeRepo:
        def close(self) -> None:
            repo_closes.append("closed")

    class FakeRegistry:
        def __init__(self, _config) -> None:
            self.load_calls = 0

        def load_all(self) -> None:
            self.load_calls += 1

    monkeypatch.setattr(cli.atexit, "register", lambda callback: registered_releases.append(callback))

    with pytest.raises(RuntimeError, match="boom"):
        cli._run_application_runtime(
            application_factory=_FakeApp,
            message_box=FakeMessageBox,
            window_factory=BoomWindow,
            argv=["voicejournal"],
            config_factory=lambda: _FakeConfig(tmp_path),
            lock_factory=FakeLock,
            repo_factory=lambda _path: FakeRepo(),
            reap_orphan_photo_dirs_fn=lambda _photos_dir, _repo: [],
            maintain_fts_fn=lambda _repo: False,
            model_registry_factory=FakeRegistry,
            theme_installer=lambda _app, _config: None,
        )

    assert len(registered_releases) == 2
    assert releases == ["released"]
    assert repo_closes == ["closed"]


def test_run_application_runtime_initializes_phase1_foundations_before_clean_exit(monkeypatch, tmp_path) -> None:
    apps: list[_FakeApp] = []
    windows: list[_FakeWindow] = []
    releases: list[str] = []
    registered_releases: list[object] = []
    theme_calls: list[tuple[_FakeApp, _FakeConfig]] = []
    repo_paths: list[Path] = []
    repo_closes: list[str] = []
    maintenance_calls: list[tuple[str, object]] = []
    registries: list[object] = []

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

    def make_window(**kwargs) -> _FakeWindow:
        window = _FakeWindow(**kwargs)
        windows.append(window)
        return window

    class FakeMessageBox:
        @staticmethod
        def information(parent, title: str, message: str) -> None:
            raise AssertionError("already-running alert should not be shown")

    class FakeRepo:
        def __init__(self, db_path: Path) -> None:
            repo_paths.append(db_path)

        def close(self) -> None:
            repo_closes.append("closed")

    class FakeRegistry:
        def __init__(self, config) -> None:
            self.config = config
            self.load_calls = 0
            registries.append(self)

        def load_all(self) -> None:
            self.load_calls += 1

    monkeypatch.setattr(cli.atexit, "register", lambda callback: registered_releases.append(callback))

    result = cli._run_application_runtime(
        application_factory=make_app,
        message_box=FakeMessageBox,
        window_factory=make_window,
        argv=["voicejournal"],
        config_factory=lambda: _FakeConfig(tmp_path),
        lock_factory=FakeLock,
        repo_factory=FakeRepo,
        reap_orphan_photo_dirs_fn=lambda photos_dir, repo: maintenance_calls.append(("reap", photos_dir)) or [],
        maintain_fts_fn=lambda repo: maintenance_calls.append(("fts", repo)) or False,
        model_registry_factory=FakeRegistry,
        theme_installer=lambda app, config: theme_calls.append((app, config)),
    )

    assert result == 5
    assert len(apps) == 1
    assert apps[0].application_name == "VoiceJournal"
    assert apps[0].organization_name == "VoiceJournal"
    assert len(windows) == 1
    assert windows[0].shown is True
    assert isinstance(windows[0].config, _FakeConfig)
    assert windows[0].repo is not None
    assert windows[0].registry is registries[0]
    assert len(registered_releases) == 2
    assert len(theme_calls) == 1
    assert repo_paths == [tmp_path / "data" / "journal.db"]
    assert maintenance_calls[0] == ("reap", tmp_path / "data" / "photos")
    assert maintenance_calls[1][0] == "fts"
    assert len(registries) == 1
    assert registries[0].load_calls == 1
    assert repo_closes == ["closed"]
    assert releases == ["released"]