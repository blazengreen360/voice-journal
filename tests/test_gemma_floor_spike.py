from __future__ import annotations

import json

from voicejournal.app.spikes.gemma_floor import build_gemma_floor_report, run_gemma_floor_spike


def test_build_gemma_floor_report_handles_missing_model(tmp_path) -> None:
    report = build_gemma_floor_report(tmp_path / "missing.gguf")

    assert report["ok"] is False
    assert report["load_ok"] is False
    assert report["close_ok"] is True
    assert report["report_write_ok"] is True
    assert report["model_exists"] is False
    assert report["model_size_bytes"] is None
    assert report["load_duration_seconds"] == 0.0
    assert str(report["error"]).startswith("Model file not found:")


def test_build_gemma_floor_report_records_success_and_closes_model(tmp_path) -> None:
    model_path = tmp_path / "gemma.gguf"
    model_path.write_bytes(b"gguf")
    closed: list[bool] = []

    class FakeModel:
        def close(self) -> None:
            closed.append(True)

    timestamps = iter([10.0, 11.25])

    report = build_gemma_floor_report(
        model_path,
        n_ctx=8192,
        n_gpu_layers=-1,
        verbose=True,
        load_model=lambda path, n_ctx, n_gpu_layers, verbose: FakeModel(),
        time_source=lambda: next(timestamps),
    )

    assert report["ok"] is True
    assert report["load_ok"] is True
    assert report["close_ok"] is True
    assert report["report_write_ok"] is True
    assert report["error"] is None
    assert report["model_exists"] is True
    assert report["model_size_bytes"] == 4
    assert report["n_ctx"] == 8192
    assert report["n_gpu_layers"] == -1
    assert report["load_duration_seconds"] == 1.25
    assert closed == [True]


def test_build_gemma_floor_report_records_loader_failure(tmp_path) -> None:
    model_path = tmp_path / "gemma.gguf"
    model_path.write_bytes(b"gguf")
    timestamps = iter([1.0, 1.5])

    report = build_gemma_floor_report(
        model_path,
        load_model=lambda path, n_ctx, n_gpu_layers, verbose: (_ for _ in ()).throw(RuntimeError("boom")),
        time_source=lambda: next(timestamps),
    )

    assert report["ok"] is False
    assert report["load_ok"] is False
    assert report["close_ok"] is True
    assert report["report_write_ok"] is True
    assert report["error"] == "RuntimeError: boom"
    assert report["load_duration_seconds"] == 0.5


def test_build_gemma_floor_report_records_close_failure(tmp_path) -> None:
    model_path = tmp_path / "gemma.gguf"
    model_path.write_bytes(b"gguf")

    class FakeModel:
        def close(self) -> None:
            raise RuntimeError("close boom")

    timestamps = iter([2.0, 2.75])

    report = build_gemma_floor_report(
        model_path,
        load_model=lambda path, n_ctx, n_gpu_layers, verbose: FakeModel(),
        time_source=lambda: next(timestamps),
    )

    assert report["ok"] is False
    assert report["load_ok"] is True
    assert report["close_ok"] is False
    assert report["report_write_ok"] is True
    assert report["error"] == "Failed to close model: RuntimeError: close boom"
    assert report["close_error"] == "RuntimeError: close boom"
    assert report["load_duration_seconds"] == 0.75


def test_run_gemma_floor_spike_writes_json_report(tmp_path, monkeypatch) -> None:
    report_path = tmp_path / "report.json"
    expected_report = {
        "ok": True,
        "load_ok": True,
        "close_ok": True,
        "report_write_ok": True,
        "error": None,
        "model_path": "/tmp/gemma.gguf",
        "model_exists": True,
        "model_size_bytes": 4,
        "n_ctx": 4096,
        "n_gpu_layers": None,
        "llama_cpp_python_version": "0.0.0",
        "python_version": "3.10.0",
        "platform": "test-platform",
        "machine": "test-machine",
        "system_total_memory_bytes": 123,
        "frozen": False,
        "executable": "/tmp/python",
        "load_duration_seconds": 0.25,
    }

    monkeypatch.setattr(
        "voicejournal.app.spikes.gemma_floor.build_gemma_floor_report",
        lambda *args, **kwargs: expected_report,
    )

    result = run_gemma_floor_spike("/tmp/gemma.gguf", output_path=str(report_path))

    assert result == 0
    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        **expected_report,
        "output_path": str(report_path),
    }


def test_run_gemma_floor_spike_prints_json_when_output_write_fails(tmp_path, monkeypatch, capsys) -> None:
    expected_report = {
        "ok": True,
        "load_ok": True,
        "close_ok": True,
        "report_write_ok": True,
        "error": None,
        "model_path": "/tmp/gemma.gguf",
        "model_exists": True,
        "model_size_bytes": 4,
        "n_ctx": 4096,
        "n_gpu_layers": None,
        "llama_cpp_python_version": "0.0.0",
        "python_version": "3.10.0",
        "platform": "test-platform",
        "machine": "test-machine",
        "system_total_memory_bytes": 123,
        "frozen": False,
        "executable": "/tmp/python",
        "load_duration_seconds": 0.25,
    }
    output_path = tmp_path / "missing" / "report.json"

    monkeypatch.setattr(
        "voicejournal.app.spikes.gemma_floor.build_gemma_floor_report",
        lambda *args, **kwargs: expected_report,
    )

    result = run_gemma_floor_spike("/tmp/gemma.gguf", output_path=str(output_path))

    captured = json.loads(capsys.readouterr().out)
    assert result == 1
    assert captured["ok"] is False
    assert captured["load_ok"] is True
    assert captured["close_ok"] is True
    assert captured["report_write_ok"] is False
    assert captured["output_path"] == str(output_path)
    assert captured["error"].startswith("Failed to write report:")
    assert captured["output_write_error"].startswith("FileNotFoundError:")


def test_run_gemma_floor_spike_expands_home_relative_output_path(tmp_path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    report_path = home_dir / "report.json"
    expected_report = {
        "ok": True,
        "load_ok": True,
        "close_ok": True,
        "report_write_ok": True,
        "error": None,
        "model_path": "/tmp/gemma.gguf",
        "model_exists": True,
        "model_size_bytes": 4,
        "n_ctx": 4096,
        "n_gpu_layers": None,
        "llama_cpp_python_version": "0.0.0",
        "python_version": "3.10.0",
        "platform": "test-platform",
        "machine": "test-machine",
        "system_total_memory_bytes": 123,
        "frozen": False,
        "executable": "/tmp/python",
        "load_duration_seconds": 0.25,
    }

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(
        "voicejournal.app.spikes.gemma_floor.build_gemma_floor_report",
        lambda *args, **kwargs: expected_report,
    )

    result = run_gemma_floor_spike("/tmp/gemma.gguf", output_path="~/report.json")

    assert result == 0
    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        **expected_report,
        "output_path": str(report_path),
    }