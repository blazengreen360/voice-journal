from __future__ import annotations

import ctypes
from importlib import metadata
import json
import os
import platform
from pathlib import Path
import sys
import time
from typing import Any, Callable


DEFAULT_N_CTX = 4096


def build_gemma_floor_report(
    model_path: Path,
    *,
    n_ctx: int = DEFAULT_N_CTX,
    n_gpu_layers: int | None = None,
    verbose: bool = False,
    load_model: Callable[[Path, int, int | None, bool], Any] | None = None,
    time_source: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    resolved_model_path = model_path.expanduser().resolve(strict=False)
    model_exists = resolved_model_path.is_file()
    report: dict[str, object] = {
        "ok": False,
        "load_ok": False,
        "close_ok": True,
        "report_write_ok": True,
        "error": None,
        "load_duration_seconds": 0.0,
        "model_path": str(resolved_model_path),
        "model_exists": model_exists,
        "model_size_bytes": resolved_model_path.stat().st_size if model_exists else None,
        "n_ctx": n_ctx,
        "n_gpu_layers": n_gpu_layers,
        "llama_cpp_python_version": _llama_cpp_python_version(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "system_total_memory_bytes": _system_total_memory_bytes(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
    }

    if not model_exists:
        report["error"] = f"Model file not found: {resolved_model_path}"
        return report

    started_at = time_source()
    model: Any | None = None
    try:
        model = (load_model or _load_llama_model)(resolved_model_path, n_ctx, n_gpu_layers, verbose)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    else:
        report["load_ok"] = True
        report["ok"] = True
    finally:
        report["load_duration_seconds"] = round(time_source() - started_at, 3)
        close_error = _close_model(model)
        if close_error is not None:
            report["ok"] = False
            report["close_ok"] = False
            if report["error"] is None:
                report["error"] = f"Failed to close model: {close_error}"
            report["close_error"] = close_error

    return report


def run_gemma_floor_spike(
    model_path: str,
    *,
    output_path: str | None = None,
    n_ctx: int = DEFAULT_N_CTX,
    n_gpu_layers: int | None = None,
    verbose: bool = False,
) -> int:
    report = build_gemma_floor_report(
        Path(model_path),
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        verbose=verbose,
    )
    expanded_output_path = Path(output_path).expanduser() if output_path else None
    if output_path:
        report["output_path"] = str(expanded_output_path)

    if output_path:
        try:
            expanded_output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as exc:
            report["ok"] = False
            report["report_write_ok"] = False
            if report["error"] is None:
                report["error"] = f"Failed to write report: {type(exc).__name__}: {exc}"
            report["output_write_error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))

    return 0 if bool(report["ok"]) else 1


def _llama_cpp_python_version() -> str | None:
    try:
        return metadata.version("llama-cpp-python")
    except metadata.PackageNotFoundError:
        return None


def _load_llama_model(
    model_path: Path,
    n_ctx: int,
    n_gpu_layers: int | None,
    verbose: bool,
) -> Any:
    from llama_cpp import Llama

    kwargs: dict[str, Any] = {
        "model_path": str(model_path),
        "n_ctx": n_ctx,
        "verbose": verbose,
    }
    if n_gpu_layers is not None:
        kwargs["n_gpu_layers"] = n_gpu_layers
    return Llama(**kwargs)


def _close_model(model: Any | None) -> str | None:
    if model is None:
        return None
    close = getattr(model, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
    return None


def _system_total_memory_bytes() -> int | None:
    page_size = _sysconf_int("SC_PAGE_SIZE")
    physical_pages = _sysconf_int("SC_PHYS_PAGES")
    if page_size is not None and physical_pages is not None:
        return page_size * physical_pages

    if sys.platform != "win32":
        return None

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.ullTotalPhys)
    return None


def _sysconf_int(name: str) -> int | None:
    if not hasattr(os, "sysconf"):
        return None
    try:
        return int(os.sysconf(name))
    except (OSError, ValueError):
        return None