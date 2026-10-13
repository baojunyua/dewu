from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    adb_path: str = r"D:\leidian\LDPlayer9\adb.exe"
    device_serial: str = "emulator-5554"
    host: str = "127.0.0.1"
    port: int = 8765
    upload_dir: Path = ROOT / "uploads"
    screenshot_dir: Path = ROOT / "screenshots"

    # 媒体清理：查价截图默认保留 24h / 最多 80 张；上传图 6h / 40 张
    screenshot_max_age_hours: float = 24.0
    screenshot_keep_newest: int = 80
    upload_max_age_hours: float = 6.0
    upload_keep_newest: int = 40
    probe_max_age_hours: float = 0.0  # _probe_ 等调试文件每次清理即删
    cleanup_interval_minutes: float = 60.0


settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
