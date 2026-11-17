"""
Configuration management for Telegram Media Downloader.
Stores all user preferences in JSON.
"""
import os
import json
import sys
from pathlib import Path


def get_app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    app_dir = base / "TelegramMediaDownloader"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_downloads_dir() -> Path:
    dl = Path.home() / "Downloads" / "TelegramMedia"
    dl.mkdir(parents=True, exist_ok=True)
    return dl


class Config:
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
        self._data["api_id"] = value; self.save()

    @property
    def api_hash(self) -> str | None:
        return self._data.get("api_hash")
    @api_hash.setter
    def api_hash(self, value: str):
        self._data["api_hash"] = value; self.save()

    @property
    def phone(self) -> str | None:
        return self._data.get("phone")
    @phone.setter
    def phone(self, value: str):
        self._data["phone"] = value; self.save()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_id and self.api_hash)

    @property
    def download_dir(self) -> str:
        return self._data.get("download_dir", str(get_downloads_dir()))
    @download_dir.setter
    def download_dir(self, value: str):
        self._data["download_dir"] = value; self.save()

    @property
    def concurrent_downloads(self) -> int:
        return self._data.get("concurrent_downloads", 8)
    @concurrent_downloads.setter
    def concurrent_downloads(self, value: int):
        self._data["concurrent_downloads"] = max(1, min(16, value)); self.save()

    @property
    def concurrent_channels(self) -> int:
        return self._data.get("concurrent_channels", 2)
    @concurrent_channels.setter
    def concurrent_channels(self, value: int):
        self._data["concurrent_channels"] = max(1, min(6, value)); self.save()

    @property
    def media_types(self) -> str:
        return self._data.get("media_types", "all")
    @media_types.setter
    def media_types(self, value: str):
        self._data["media_types"] = value; self.save()

    @property
    def date_from(self) -> str | None:
        return self._data.get("date_from")
    @date_from.setter
    def date_from(self, value: str | None):
        self._data["date_from"] = value; self.save()

    @property
    def date_to(self) -> str | None:
        return self._data.get("date_to")
    @date_to.setter
    def date_to(self, value: str | None):
        self._data["date_to"] = value; self.save()

    @property
    def minimize_to_tray(self) -> bool:
        return self._data.get("minimize_to_tray", True)
    @minimize_to_tray.setter
    def minimize_to_tray(self, value: bool):
        self._data["minimize_to_tray"] = value; self.save()

    @property
    def notify_on_complete(self) -> bool:
        return self._data.get("notify_on_complete", True)
    @notify_on_complete.setter
    def notify_on_complete(self, value: bool):
        self._data["notify_on_complete"] = value; self.save()

    def clear(self):
        self._data = {}
        if self.config_path.exists():
            self.config_path.unlink()
