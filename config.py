"""
Configuration management for Telegram Media Downloader.
Stores api_id, api_hash, and download settings in user's AppData.
"""
import os
import json
import sys
from pathlib import Path


def get_app_dir() -> Path:
    """Get the application data directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    app_dir = base / "TelegramMediaDownloader"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_downloads_dir() -> Path:
    """Get the default downloads directory."""
    home = Path.home()
    dl = home / "Downloads" / "TelegramMedia"
    dl.mkdir(parents=True, exist_ok=True)
    return dl


class Config:
    """Application configuration with JSON persistence."""

    def __init__(self):
        self.app_dir = get_app_dir()
        self.config_path = self.app_dir / "config.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def save(self):
        with open(self.config_path, "w") as f:
            json.dump(self._data, f, indent=2)

    @property
    def api_id(self) -> int | None:
        return self._data.get("api_id")

    @api_id.setter
    def api_id(self, value: int):
        self._data["api_id"] = value
        self.save()

    @property
    def api_hash(self) -> str | None:
        return self._data.get("api_hash")

    @api_hash.setter
    def api_hash(self, value: str):
        self._data["api_hash"] = value
        self.save()

    @property
    def phone(self) -> str | None:
        return self._data.get("phone")

    @phone.setter
    def phone(self, value: str):
        self._data["phone"] = value
        self.save()

    @property
    def download_dir(self) -> str:
        return self._data.get("download_dir", str(get_downloads_dir()))

    @download_dir.setter
    def download_dir(self, value: str):
        self._data["download_dir"] = value
        self.save()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_id and self.api_hash)

    def clear(self):
        self._data = {}
        if self.config_path.exists():
            self.config_path.unlink()
