from voicejournal.app.config import AppConfig


def test_assets_package_is_resolvable() -> None:
    assets_dir = AppConfig().assets_dir()
    theme = assets_dir.joinpath("style").joinpath("light.qss")

    assert theme.is_file()
    assert "${surface}" in theme.read_text(encoding="utf-8")

