from __future__ import annotations

import json

from voicejournal.app.packaging_smoke import build_packaging_smoke_report, run_packaging_smoke


def test_build_packaging_smoke_report_checks_both_themes(tmp_path, monkeypatch) -> None:
    assets_dir = tmp_path / "assets"
    style_dir = assets_dir / "style"
    style_dir.mkdir(parents=True)
    (style_dir / "light.qss").write_text("${surface}\n", encoding="utf-8")
    (style_dir / "dark.qss").write_text("${surface}\n", encoding="utf-8")
    model_path = assets_dir / "models" / "silero_vad.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"vad")

    class FakeConfig:
        def assets_dir(self):
            return assets_dir

        def theme_asset(self, theme_name: str):
            return style_dir / f"{theme_name}.qss"

    monkeypatch.setattr("voicejournal.app.packaging_smoke.AppConfig", FakeConfig)
    monkeypatch.setattr("voicejournal.app.packaging_smoke.bundled_silero_vad_model", lambda: model_path)
    monkeypatch.setattr("voicejournal.app.packaging_smoke.SILERO_VAD_SHA256", "651bfad0aa5b42c5a5b8dad76f49c4a122fe1d1658fc55cfdd1ea9923fe3fd9e")

    report = build_packaging_smoke_report()

    assert report["ok"] is True
    assert report["light_theme_exists"] is True
    assert report["dark_theme_exists"] is True
    assert report["light_theme_contains_token"] is True
    assert report["dark_theme_contains_token"] is True
    assert report["theme_exists"] is True
    assert report["theme_contains_token"] is True
    assert report["model_sha256"] == "651bfad0aa5b42c5a5b8dad76f49c4a122fe1d1658fc55cfdd1ea9923fe3fd9e"
    assert report["error"] is None
    assert report["read_errors"] == []


def test_build_packaging_smoke_report_fails_on_model_hash_mismatch(tmp_path, monkeypatch) -> None:
    assets_dir = tmp_path / "assets"
    style_dir = assets_dir / "style"
    style_dir.mkdir(parents=True)
    (style_dir / "light.qss").write_text("${surface}\n", encoding="utf-8")
    (style_dir / "dark.qss").write_text("${surface}\n", encoding="utf-8")
    model_path = assets_dir / "models" / "silero_vad.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"vad")

    class FakeConfig:
        def assets_dir(self):
            return assets_dir

        def theme_asset(self, theme_name: str):
            return style_dir / f"{theme_name}.qss"

    monkeypatch.setattr("voicejournal.app.packaging_smoke.AppConfig", FakeConfig)
    monkeypatch.setattr("voicejournal.app.packaging_smoke.bundled_silero_vad_model", lambda: model_path)
    monkeypatch.setattr("voicejournal.app.packaging_smoke.SILERO_VAD_SHA256", "not-the-right-hash")

    report = build_packaging_smoke_report()

    assert report["model_exists"] is True
    assert report["model_sha256"] == "651bfad0aa5b42c5a5b8dad76f49c4a122fe1d1658fc55cfdd1ea9923fe3fd9e"
    assert report["ok"] is False


def test_build_packaging_smoke_report_fails_when_dark_theme_lacks_token(tmp_path, monkeypatch) -> None:
    assets_dir = tmp_path / "assets"
    style_dir = assets_dir / "style"
    style_dir.mkdir(parents=True)
    (style_dir / "light.qss").write_text("${surface}\n", encoding="utf-8")
    (style_dir / "dark.qss").write_text("background: black;\n", encoding="utf-8")
    model_path = assets_dir / "models" / "silero_vad.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"vad")

    class FakeConfig:
        def assets_dir(self):
            return assets_dir

        def theme_asset(self, theme_name: str):
            return style_dir / f"{theme_name}.qss"

    monkeypatch.setattr("voicejournal.app.packaging_smoke.AppConfig", FakeConfig)
    monkeypatch.setattr("voicejournal.app.packaging_smoke.bundled_silero_vad_model", lambda: model_path)
    monkeypatch.setattr("voicejournal.app.packaging_smoke.SILERO_VAD_SHA256", "651bfad0aa5b42c5a5b8dad76f49c4a122fe1d1658fc55cfdd1ea9923fe3fd9e")

    report = build_packaging_smoke_report()

    assert report["ok"] is False
    assert report["light_theme_contains_token"] is True
    assert report["dark_theme_contains_token"] is False
    assert report["theme_contains_token"] is False


def test_build_packaging_smoke_report_captures_read_errors(monkeypatch) -> None:
    class BrokenAsset:
        def __init__(self, label: str, *, read_text_error: Exception | None = None, read_bytes_error: Exception | None = None) -> None:
            self._label = label
            self._read_text_error = read_text_error
            self._read_bytes_error = read_bytes_error

        def is_file(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:
            assert encoding == "utf-8"
            if self._read_text_error is not None:
                raise self._read_text_error
            return "${surface}"

        def read_bytes(self) -> bytes:
            if self._read_bytes_error is not None:
                raise self._read_bytes_error
            return b"vad"

        def __str__(self) -> str:
            return self._label

    class FakeConfig:
        def assets_dir(self):
            return "/tmp/assets"

        def theme_asset(self, theme_name: str):
            if theme_name == "light":
                return BrokenAsset("light.qss", read_text_error=OSError("no read"))
            return BrokenAsset("dark.qss")

    monkeypatch.setattr("voicejournal.app.packaging_smoke.AppConfig", FakeConfig)
    monkeypatch.setattr(
        "voicejournal.app.packaging_smoke.bundled_silero_vad_model",
        lambda: BrokenAsset("silero_vad.onnx", read_bytes_error=OSError("no bytes")),
    )

    report = build_packaging_smoke_report()

    assert report["ok"] is False
    assert report["light_theme_contains_token"] is False
    assert report["dark_theme_contains_token"] is True
    assert report["model_sha256"] is None
    assert len(report["read_errors"]) == 2
    assert report["error"] is not None


def test_run_packaging_smoke_prints_json_when_output_write_fails(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "voicejournal.app.packaging_smoke.build_packaging_smoke_report",
        lambda: {"ok": True, "theme_exists": True, "theme_contains_token": True, "model_exists": True},
    )

    output_path = tmp_path / "missing" / "smoke.json"
    result = run_packaging_smoke(str(output_path))

    captured = json.loads(capsys.readouterr().out)
    assert result == 1
    assert captured["ok"] is False
    assert captured["output_path"] == str(output_path)
    assert captured["output_write_error"].startswith("FileNotFoundError:")
    assert captured["error"].startswith("Failed to write smoke report:")


def test_run_packaging_smoke_prints_json_when_report_build_fails(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "voicejournal.app.packaging_smoke.build_packaging_smoke_report",
        lambda: (_ for _ in ()).throw(ModuleNotFoundError("voicejournal.assets missing")),
    )

    result = run_packaging_smoke()

    captured = json.loads(capsys.readouterr().out)
    assert result == 1
    assert captured["ok"] is False
    assert captured["error"] == "Failed to build smoke report: ModuleNotFoundError: voicejournal.assets missing"