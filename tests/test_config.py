import pytest

from voicejournal.app import config as config_module
from voicejournal.app.config import AppConfig, DEFAULT_SPEECH_RATE


def test_assets_package_is_resolvable() -> None:
    config = AppConfig()
    assets_dir = config.assets_dir()
    theme = config.theme_asset("light")

    assert theme.is_file()
    assert assets_dir.joinpath("style").joinpath("light.qss").is_file()
    assert "${surface}" in theme.read_text(encoding="utf-8")


def test_theme_asset_supports_both_bundled_themes() -> None:
    config = AppConfig()
    light_theme = config.theme_asset("light")
    dark_theme = config.theme_asset("dark")

    assert light_theme.is_file()
    assert dark_theme.is_file()
    assert light_theme.name == "light.qss"
    assert dark_theme.name == "dark.qss"
    assert light_theme.name != dark_theme.name


def test_theme_asset_rejects_unknown_theme() -> None:
    with pytest.raises(ValueError, match="Unsupported theme"):
        AppConfig().theme_asset("sepia")


def test_speech_rate_defaults_to_calm_pace() -> None:
    assert AppConfig().speech_rate == DEFAULT_SPEECH_RATE
    assert DEFAULT_SPEECH_RATE < 1.0


def test_ensure_directories_resolves_all_user_paths(monkeypatch, tmp_path) -> None:
    class FakePlatformDirs:
        def __init__(self, appname: str, appauthor: str, roaming: bool) -> None:
            self.user_config_dir = str(tmp_path / "config")
            self.user_data_dir = str(tmp_path / "data")
            self.user_cache_dir = str(tmp_path / "cache")

    monkeypatch.setattr(config_module, "PlatformDirs", FakePlatformDirs)

    paths = AppConfig().ensure_directories()

    assert paths.config_dir == tmp_path / "config"
    assert paths.data_dir == tmp_path / "data"
    assert paths.cache_dir == tmp_path / "cache"
    assert paths.logs_dir == tmp_path / "data" / "logs"
    assert paths.models_dir == tmp_path / "data" / "models"
    assert paths.photos_dir == tmp_path / "data" / "photos"
    assert paths.settings_file == tmp_path / "config" / "settings.json"
    assert paths.database_file == tmp_path / "data" / "journal.db"
    assert paths.lock_file == tmp_path / "data" / "journal.db.lock"

    assert paths.config_dir.is_dir()
    assert paths.data_dir.is_dir()
    assert paths.cache_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.models_dir.is_dir()
    assert paths.photos_dir.is_dir()

