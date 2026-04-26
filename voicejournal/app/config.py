from __future__ import annotations

from dataclasses import dataclass
from importlib.abc import Traversable
from importlib.resources import files
from pathlib import Path

from platformdirs import PlatformDirs


APP_NAME = "VoiceJournal"
APP_AUTHOR = "VoiceJournal"


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    logs_dir: Path
    models_dir: Path
    photos_dir: Path

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "journal.db"

    @property
    def lock_file(self) -> Path:
        return self.database_file.with_suffix(".db.lock")


class AppConfig:
    def __init__(self, app_name: str = APP_NAME, app_author: str = APP_AUTHOR) -> None:
        self._dirs = PlatformDirs(appname=app_name, appauthor=app_author, roaming=False)

    def paths(self) -> AppPaths:
        data_dir = Path(self._dirs.user_data_dir)
        return AppPaths(
            config_dir=Path(self._dirs.user_config_dir),
            data_dir=data_dir,
            cache_dir=Path(self._dirs.user_cache_dir),
            logs_dir=data_dir / "logs",
            models_dir=data_dir / "models",
            photos_dir=data_dir / "photos",
        )

    def ensure_directories(self) -> AppPaths:
        paths = self.paths()
        for path in (
            paths.config_dir,
            paths.data_dir,
            paths.cache_dir,
            paths.logs_dir,
            paths.models_dir,
            paths.photos_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return paths

    def assets_dir(self) -> Traversable:
        return files("voicejournal.assets")
