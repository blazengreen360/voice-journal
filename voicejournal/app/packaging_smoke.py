from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from voicejournal.app.bundled_assets import SILERO_VAD_SHA256, bundled_silero_vad_model
from voicejournal.app.config import AppConfig


def build_packaging_smoke_report() -> dict[str, object]:
    assets_dir = AppConfig().assets_dir()
    theme = assets_dir.joinpath("style").joinpath("light.qss")
    model = bundled_silero_vad_model()

    theme_exists = theme.is_file()
    model_exists = model.is_file()
    theme_contains_token = theme_exists and "${surface}" in theme.read_text(encoding="utf-8")
    model_sha256 = hashlib.sha256(model.read_bytes()).hexdigest() if model_exists else None

    return {
        "ok": theme_exists and theme_contains_token and model_exists and model_sha256 == SILERO_VAD_SHA256,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "assets_dir": str(assets_dir),
        "theme_exists": theme_exists,
        "theme_contains_token": theme_contains_token,
        "model_exists": model_exists,
        "model_sha256": model_sha256,
    }


def run_packaging_smoke(output_path: str | None = None) -> int:
    report = build_packaging_smoke_report()
    serialized = json.dumps(report, indent=2, sort_keys=True)

    if output_path:
        Path(output_path).write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)

    return 0 if bool(report["ok"]) else 1
