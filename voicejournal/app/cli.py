from __future__ import annotations

import argparse
import atexit
import os
import sys
from typing import Sequence

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
) -> int:
    qt_app = application_factory(list(argv))

    config = config_factory()
    paths = config.ensure_directories()

    lock = lock_factory(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError:
        message_box.information(None, "VoiceJournal", ALREADY_RUNNING_MESSAGE)
        return 0

    atexit.register(lock.release)
    try:
        qt_app.setApplicationName("VoiceJournal")
        qt_app.setOrganizationName("VoiceJournal")

        window = window_factory()
        window.show()
        return qt_app.exec()
    finally:
        lock.release()
