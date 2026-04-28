from __future__ import annotations

import argparse
import atexit
import os
import sys
from typing import Callable, Sequence

from voicejournal.app.config import AppConfig
from voicejournal.app.packaging_smoke import run_packaging_smoke
from voicejournal.app.spikes import run_gemma_floor_spike
from voicejournal.app.single_instance import AlreadyRunningError, SingleInstanceLock


ALREADY_RUNNING_MESSAGE = "VoiceJournal is already running. Switch to the existing window."


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get("VOICEJOURNAL_SMOKE") == "1":
        return run_packaging_smoke(os.environ.get("VOICEJOURNAL_SMOKE_OUTPUT"))

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "spike":
        return _run_command(arguments)

    return _run_application()


def _run_command(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="voicejournal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    spike_parser = subparsers.add_parser("spike", help="Run validation spikes")
    spike_subparsers = spike_parser.add_subparsers(dest="spike_name", required=True)

    gemma_parser = spike_subparsers.add_parser(
        "gemma-floor",
        help="Probe whether the current llama-cpp-python build can load the default Gemma 4 E2B artifact",
    )
    gemma_parser.add_argument("--model-path", required=True, help="Path to gemma-4-E2B-it-Q8_0.gguf")
    gemma_parser.add_argument("--output", help="Write the JSON report to this path")
    gemma_parser.add_argument("--n-ctx", type=int, default=4096, help="Context size to use for the load probe")
    gemma_parser.add_argument("--n-gpu-layers", type=int, help="Optional GPU offload override")
    gemma_parser.add_argument("--verbose", action="store_true", help="Enable llama.cpp loader logs")

    args = parser.parse_args(list(argv))
    if args.command == "spike" and args.spike_name == "gemma-floor":
        return run_gemma_floor_spike(
            args.model_path,
            output_path=args.output,
            n_ctx=args.n_ctx,
            n_gpu_layers=args.n_gpu_layers,
            verbose=args.verbose,
        )

    raise AssertionError(f"Unhandled command: {args.command}")


def _run_application() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from voicejournal.app.ui.main_window import MainWindow

    return _run_application_runtime(
        application_factory=QApplication,
        message_box=QMessageBox,
        window_factory=MainWindow,
        argv=sys.argv,
    )


def _run_application_runtime(
    *,
    application_factory,
    message_box,
    window_factory,
    argv: Sequence[str],
    config_factory=AppConfig,
    lock_factory=SingleInstanceLock,
    repo_factory=None,
    reap_orphan_photo_dirs_fn=None,
    maintain_fts_fn=None,
    model_registry_factory=None,
    theme_installer=None,
) -> int:
    from voicejournal.app.core.local_repo import LocalRepo
    from voicejournal.app.maintenance import maintain_fts, reap_orphan_photo_dirs
    from voicejournal.app.model_registry import ModelRegistry
    from voicejournal.app.ui.theme import install_app_theme

    qt_app = application_factory(list(argv))

    config = config_factory()
    install_theme = install_app_theme if theme_installer is None else theme_installer
    install_theme(qt_app, config)
    paths = config.ensure_directories()
    make_repo = LocalRepo if repo_factory is None else repo_factory
    reap_photos = reap_orphan_photo_dirs if reap_orphan_photo_dirs_fn is None else reap_orphan_photo_dirs_fn
    keep_fts = maintain_fts if maintain_fts_fn is None else maintain_fts_fn
    make_registry = ModelRegistry if model_registry_factory is None else model_registry_factory

    lock = lock_factory(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError:
        message_box.information(None, "VoiceJournal", ALREADY_RUNNING_MESSAGE)
        return 0

    release_lock = _once(lock.release)
    close_repo: Callable[[], None] = lambda: None
    atexit.register(release_lock)
    try:
        repo = make_repo(paths.database_file)
        close_repo = _once(repo.close)
        atexit.register(close_repo)
        reap_photos(paths.photos_dir, repo)
        keep_fts(repo)
        registry = make_registry(config)

        qt_app.setApplicationName("VoiceJournal")
        qt_app.setOrganizationName("VoiceJournal")

        window = window_factory(config=config, repo=repo, registry=registry)
        window.show()
        registry.load_all()
        return qt_app.exec()
    finally:
        close_repo()
        release_lock()


def _once(callback: Callable[[], None]) -> Callable[[], None]:
    called = False

    def wrapped() -> None:
        nonlocal called
        if called:
            return
        called = True
        callback()

    return wrapped
