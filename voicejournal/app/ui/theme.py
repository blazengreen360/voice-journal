from __future__ import annotations

from string import Template

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QPalette

from voicejournal.app.config import AppConfig


THEME_TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "surface": "#F6F1EA",
        "text_primary": "#1F242C",
    },
    "dark": {
        "surface": "#171A20",
        "text_primary": "#F1F3F6",
    },
}

_REFRESH_EVENTS = {
    QEvent.Type.ApplicationPaletteChange,
    QEvent.Type.PaletteChange,
    QEvent.Type.StyleChange,
}


def render_qss(config: AppConfig, theme_name: str) -> str:
    template_text = config.theme_asset(theme_name).read_text(encoding="utf-8")
    return Template(template_text).substitute(THEME_TOKENS[theme_name])


def detect_system_theme(app) -> str:
    window_color = app.palette().color(QPalette.ColorRole.Window)
    return "dark" if window_color.lightness() < 128 else "light"


class ThemeController(QObject):
    def __init__(self, app, config: AppConfig) -> None:
        parent = app if isinstance(app, QObject) else None
        super().__init__(parent)
        self._app = app
        self._config = config
        self._current_theme: str | None = None

    @property
    def current_theme(self) -> str | None:
        return self._current_theme

    def apply_theme(self, theme_name: str) -> str:
        stylesheet = render_qss(self._config, theme_name)
        self._app.setStyleSheet(stylesheet)
        self._current_theme = theme_name
        return stylesheet

    def apply_system_theme(self) -> str:
        return self.apply_theme(detect_system_theme(self._app))

    def eventFilter(self, watched, event) -> bool:
        if watched is self._app and event.type() in _REFRESH_EVENTS:
            self.apply_system_theme()
        return False


def install_app_theme(app, config: AppConfig) -> ThemeController:
    controller = ThemeController(app, config)
    app.installEventFilter(controller)
    controller.apply_system_theme()
    return controller


__all__ = [
    "THEME_TOKENS",
    "ThemeController",
    "detect_system_theme",
    "install_app_theme",
    "render_qss",
]