from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent

from voicejournal.app.config import AppConfig
from voicejournal.app.ui.theme import ThemeController, install_app_theme, render_qss


class _FakeColor:
    def __init__(self, lightness_value: int) -> None:
        self._lightness_value = lightness_value

    def lightness(self) -> int:
        return self._lightness_value


class _FakePalette:
    def __init__(self, lightness_value: int) -> None:
        self._lightness_value = lightness_value

    def color(self, _role) -> _FakeColor:
        return _FakeColor(self._lightness_value)


class _FakeApp:
    def __init__(self, lightness_value: int) -> None:
        self._lightness_value = lightness_value
        self.style_sheet = ""
        self.filters: list[object] = []

    def palette(self) -> _FakePalette:
        return _FakePalette(self._lightness_value)

    def setStyleSheet(self, style_sheet: str) -> None:
        self.style_sheet = style_sheet

    def installEventFilter(self, event_filter: object) -> None:
        self.filters.append(event_filter)


def test_render_qss_substitutes_bundled_light_and_dark_templates() -> None:
    config = AppConfig()

    light = render_qss(config, "light")
    dark = render_qss(config, "dark")

    assert "${" not in light
    assert "${" not in dark
    assert "#F6F1EA" in light
    assert "#171A20" in dark


def test_render_qss_fails_fast_on_missing_token(tmp_path: Path) -> None:
    theme_path = tmp_path / "broken.qss"
    theme_path.write_text("QWidget { color: ${missing}; }\n", encoding="utf-8")

    class FakeConfig:
        def theme_asset(self, _theme_name: str) -> Path:
            return theme_path

    with pytest.raises(KeyError, match="missing"):
        render_qss(FakeConfig(), "light")


def test_install_app_theme_applies_detected_theme_and_refreshes_on_palette_change() -> None:
    app = _FakeApp(lightness_value=240)
    controller = install_app_theme(app, AppConfig())

    assert isinstance(controller, ThemeController)
    assert controller.current_theme == "light"
    assert len(app.filters) == 1
    assert "#F6F1EA" in app.style_sheet

    app._lightness_value = 10
    controller.eventFilter(app, QEvent(QEvent.Type.ApplicationPaletteChange))

    assert controller.current_theme == "dark"
    assert "#171A20" in app.style_sheet