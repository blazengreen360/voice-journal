from __future__ import annotations

import certifi
import hashlib
import json
from pathlib import Path
import sys

from voicejournal.app.bundled_assets import SILERO_VAD_SHA256, bundled_silero_vad_model
from voicejournal.app.config import AppConfig


def build_packaging_smoke_report() -> dict[str, object]:
    config = AppConfig()
    assets_dir = config.assets_dir()
    light_theme = config.theme_asset("light")
    dark_theme = config.theme_asset("dark")
    model = bundled_silero_vad_model()
    read_errors: list[str] = []
    certifi_bundle_path = Path(certifi.where())

    light_theme_exists = light_theme.is_file()
    dark_theme_exists = dark_theme.is_file()
    model_exists = model.is_file()
    certifi_bundle_exists = certifi_bundle_path.is_file()
    light_theme_contains_token = _theme_contains_surface_token(light_theme, light_theme_exists, read_errors)
    dark_theme_contains_token = _theme_contains_surface_token(dark_theme, dark_theme_exists, read_errors)
    theme_exists = light_theme_exists and dark_theme_exists
    theme_contains_token = light_theme_contains_token and dark_theme_contains_token
    model_sha256 = _model_sha256(model, model_exists, read_errors)
    if not certifi_bundle_exists:
        read_errors.append(f"Missing certifi CA bundle at {certifi_bundle_path}")

    return {
        "ok": (
            theme_exists
            and theme_contains_token
            and model_exists
            and model_sha256 == SILERO_VAD_SHA256
            and certifi_bundle_exists
        ),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "assets_dir": str(assets_dir),
        "certifi_bundle_path": str(certifi_bundle_path),
        "certifi_bundle_exists": certifi_bundle_exists,
        "error": "; ".join(read_errors) if read_errors else None,
        "read_errors": read_errors,
        "light_theme_exists": light_theme_exists,
        "light_theme_contains_token": light_theme_contains_token,
        "dark_theme_exists": dark_theme_exists,
        "dark_theme_contains_token": dark_theme_contains_token,
        "theme_exists": theme_exists,
        "theme_contains_token": theme_contains_token,
        "model_exists": model_exists,
        "model_sha256": model_sha256,
    }


def run_packaging_smoke(output_path: str | None = None) -> int:
    try:
        report = build_packaging_smoke_report()
    except Exception as exc:
        report = {
            "ok": False,
            "error": f"Failed to build smoke report: {type(exc).__name__}: {exc}",
            "executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
        }
    if output_path:
        report["output_path"] = str(Path(output_path).expanduser())

    if output_path:
        expanded_output_path = Path(output_path).expanduser()
        try:
            expanded_output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as exc:
            report["ok"] = False
            report["output_write_error"] = f"{type(exc).__name__}: {exc}"
            if report.get("error") is None:
                report["error"] = f"Failed to write smoke report: {type(exc).__name__}: {exc}"
            print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))

    return 0 if bool(report["ok"]) else 1


def _theme_contains_surface_token(theme, theme_exists: bool, read_errors: list[str]) -> bool:
    if not theme_exists:
        return False
    try:
        return "${surface}" in theme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        read_errors.append(f"Failed to read theme asset {theme}: {type(exc).__name__}: {exc}")
        return False


def _model_sha256(model, model_exists: bool, read_errors: list[str]) -> str | None:
    if not model_exists:
        return None
    try:
        return hashlib.sha256(model.read_bytes()).hexdigest()
    except OSError as exc:
        read_errors.append(f"Failed to read model asset {model}: {type(exc).__name__}: {exc}")
        return None
