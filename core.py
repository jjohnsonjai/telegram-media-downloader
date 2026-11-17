"""
Core Telegram engine using Telethon.
Handles authentication, channel listing, and media downloading.
"""
import os
import sqlite3
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Callable

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    Channel,
    Chat,
)
from telethon.errors import SessionPasswordNeededError

from config import Config

logger = logging.getLogger(__name__)


class DownloadDB:
    """SQLite database to track downloaded file IDs for skip support."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS downloaded (
                    file_id TEXT PRIMARY KEY,
                    channel_id INTEGER,
                    filename TEXT,
                    downloaded_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel
                ON downloaded(channel_id)
            """)

    def is_downloaded(self, file_id: str) -> bool:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM downloaded WHERE file_id = ?", (file_id,)
            ).fetchone()
            return row is not None

    def mark_downloaded(self, file_id: str, channel_id: int, filename: str):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO downloaded VALUES (?, ?, ?, ?)",
                (file_id, channel_id, filename, datetime.now().isoformat()),
            )

    def get_stats(self) -> tuple[int, int]:
        """Return (total_downloaded, total_size_approx)."""
        with sqlite3.connect(str(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM downloaded").fetchone()[0]
            return count, 0


class TelegramEngine:
    """Core engine for Telegram operations."""

    def __init__(self, config: Config):
        self.config = config
        self.client: TelegramClient | None = None
        self.db = DownloadDB(config.app_dir / "downloads.db")
        self._progress_callback: Callable | None = None
        self._log_callback: Callable | None = None
        self._cancel_flag = False

    def set_callbacks(
        self,
        progress: Callable | None = None,
        log: Callable | None = None,
    ):
        self._progress_callback = progress
        self._log_callback = log

    def cancel(self):
        self._cancel_flag = True

    def reset_cancel(self):
        self._cancel_flag = False

    def _log(self, msg: str):
        if self._log_callback:
            self._log_callback(msg)

    def _update_progress(self, current: int, total: int):
        if self._progress_callback:
            self._progress_callback(current, total)

    async def connect(self) -> bool:
        """Create and connect the Telegram client."""
        session_path = str(self.config.app_dir / "session")
        self.client = TelegramClient(
            session_path,
            self.config.api_id,
            self.config.api_hash,
            system_version="4.16.30-vx-custom",
            device_model="Desktop",
            app_version="1.0.0",
        )
        await self.client.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str):
        """Send authentication code to phone number."""
        if not self.client:
            raise RuntimeError("Client not connected")
        return await self.client.send_code_request(phone)

    async def sign_in(self, phone: str, code: str):
        """Sign in with phone and verification code."""
        if not self.client:
            raise RuntimeError("Client not connected")
        try:
            return await self.client.sign_in(phone, code)
        except SessionPasswordNeededError:
            raise  # Let the caller handle 2FA

    async def sign_in_with_password(self, password: str):
        """Sign in with 2FA password."""
        if not self.client:
            raise RuntimeError("Client not connected")
        return await self.client.sign_in(password=password)

    async def get_me(self):
        """Get current user info."""
        if not self.client:
            raise RuntimeError("Client not connected")
        return await self.client.get_me()

    async def get_channels(self) -> list[dict]:
        """Get all channels and groups the user is a member of."""
        if not self.client:
            raise RuntimeError("Client not connected")

        channels = []
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, (Channel, Chat)):
                channels.append({
                    "id": entity.id,
                    "title": dialog.name,
                    "username": getattr(entity, "username", None),
                    "is_channel": getattr(entity, "broadcast", False),
                    "is_group": getattr(entity, "megagroup", False),
                })
        return channels

    async def download_channel_media(
        self,
        channel_id: int,
        channel_title: str,
        download_dir: str,
        progress_callback: Callable | None = None,
    ) -> dict:
        """
        Download all photos and videos from a channel.
        Returns stats dict.
        """
        if not self.client:
            raise RuntimeError("Client not connected")

        channel_dir = Path(download_dir) / self._safe_name(channel_title)
        channel_dir.mkdir(parents=True, exist_ok=True)

        stats = {"downloaded": 0, "skipped": 0, "errors": 0, "total": 0}

        # Get entity
        entity = await self.client.get_entity(channel_id)

        # Count total media first
        self._log(f"Scanning '{channel_title}' for media...")

        # Iterate messages and download media
        async for message in self.client.iter_messages(entity, wait_time=0.5):
            if self._cancel_flag:
                self._log("Download cancelled.")
                break

            media = message.media
            if not media:
                continue

            file_id = None
            ext = ""
            is_media = False

            if isinstance(media, MessageMediaPhoto):
                file_id = str(message.id)
                ext = ".jpg"
                is_media = True
            elif isinstance(media, MessageMediaDocument):
                doc = media.document
                if not doc:
                    continue
                # Check if it's a video or GIF
                mime = doc.mime_type or ""
                is_video = mime.startswith("video/") or "video" in mime
                is_gif = mime == "image/gif"
                if is_video or is_gif:
                    file_id = str(doc.id)
                    # Determine extension
                    for attr in doc.attributes:
                        name = getattr(attr, "file_name", None)
                        if name:
                            ext = Path(name).suffix.lower()
                            break
                    if not ext:
                        ext = ".mp4" if is_video else ".gif"
                    is_media = True

            if not is_media or not file_id:
                continue

            stats["total"] += 1

            # Check if already downloaded
            if self.db.is_downloaded(file_id):
                stats["skipped"] += 1
                continue

            # Build filename
            timestamp = message.date.strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{message.id}{ext}"
            filepath = channel_dir / filename

            # Skip if file exists (belt-and-suspenders)
            if filepath.exists():
                self.db.mark_downloaded(file_id, channel_id, filename)
                stats["skipped"] += 1
                continue

            # Download
            try:
                self._log(f"Downloading: {filename}")
                await self.client.download_media(
                    message,
                    str(filepath),
                    progress_callback=lambda c, t: self._on_file_progress(c, t, filename),
                )
                self.db.mark_downloaded(file_id, channel_id, filename)
                stats["downloaded"] += 1
            except Exception as e:
                self._log(f"Error downloading {filename}: {e}")
                stats["errors"] += 1

        return stats

    def _on_file_progress(self, current: int, total: int, filename: str):
        """Handle per-file progress."""
        # We pass through to the main progress callback
        pass

    @staticmethod
    def _safe_name(name: str) -> str:
        """Sanitize a name for use as a directory name."""
        invalid = '<>:"/\\|?*'
        for c in invalid:
            name = name.replace(c, "_")
        return name.strip()[:100]

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
            self.client = None
