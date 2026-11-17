"""
Core Telegram engine using Telethon.
Handles authentication, channel listing, and high-speed parallel media downloading.
"""
import sqlite3
import asyncio
import logging
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Callable
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    Channel,
    Chat,
)
from telethon.errors import SessionPasswordNeededError, FloodWaitError

from config import Config

logger = logging.getLogger(__name__)


class DownloadDB:
    """Thread-safe SQLite database to track downloaded file IDs."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS downloaded (
                    file_id TEXT PRIMARY KEY,
                    channel_id INTEGER,
                    filename TEXT,
                    downloaded_at TEXT,
                    size_bytes INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel
                ON downloaded(channel_id)
            """)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(downloaded)")]
            if "size_bytes" not in cols:
                conn.execute("ALTER TABLE downloaded ADD COLUMN size_bytes INTEGER DEFAULT 0")

    def is_downloaded(self, file_id: str) -> bool:
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                row = conn.execute(
                    "SELECT 1 FROM downloaded WHERE file_id = ?", (file_id,)
                ).fetchone()
                return row is not None

    def mark_downloaded(self, file_id: str, channel_id: int, filename: str, size_bytes: int = 0):
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO downloaded VALUES (?, ?, ?, ?, ?)",
                    (file_id, channel_id, filename, datetime.now().isoformat(), size_bytes),
                )

    def get_stats(self) -> tuple[int, int]:
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM downloaded").fetchone()[0]
                total_size = conn.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) FROM downloaded"
                ).fetchone()[0]
                return count, total_size


@dataclass
class DownloadJob:
    message_id: int
    file_id: str
    filename: str
    filepath: Path
    channel_id: int
    estimated_size: int = 0


class TelegramEngine:
    """Core engine with parallel download support."""

    DEFAULT_CONCURRENT_DOWNLOADS = 6
    DEFAULT_CONCURRENT_CHANNELS = 2
    MAX_CONCURRENT_DOWNLOADS = 12

    def __init__(self, config: Config):
        self.config = config
        self.client: TelegramClient | None = None
        self.db = DownloadDB(config.app_dir / "downloads.db")
        self._log_callback: Callable | None = None
        self._speed_callback: Callable | None = None
        self._cancel_flag = False

        self.concurrent_downloads = self.DEFAULT_CONCURRENT_DOWNLOADS
        self.concurrent_channels = self.DEFAULT_CONCURRENT_CHANNELS

        self._bytes_downloaded = 0
        self._bytes_total = 0
        self._active_count = 0
        self._speed_start = 0.0
        self._speed_lock = threading.Lock()

    def set_callbacks(self, log=None, speed=None):
        self._log_callback = log
        self._speed_callback = speed

    def cancel(self):
        self._cancel_flag = True

    def reset_cancel(self):
        self._cancel_flag = False
        self._bytes_downloaded = 0
        self._bytes_total = 0
        self._active_count = 0
        self._speed_start = 0.0

    def _log(self, msg: str):
        if self._log_callback:
            self._log_callback(msg)

    def _emit_speed(self, just_finished: int = 0):
        if not self._speed_callback:
            return
        with self._speed_lock:
            self._bytes_downloaded += just_finished
            if just_finished > 0:
                self._active_count -= 1
            elapsed = time.monotonic() - self._speed_start if self._speed_start > 0 else 0
            speed = self._bytes_downloaded / elapsed if elapsed > 0 else 0
            self._speed_callback(
                self._bytes_downloaded,
                max(self._bytes_total, self._bytes_downloaded),
                speed,
                self._active_count,
            )

    # ── Auth ───────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        session_path = str(self.config.app_dir / "session")
        self.client = TelegramClient(
            session_path,
            self.config.api_id,
            self.config.api_hash,
            system_version="4.16.30-vx-custom",
            device_model="Desktop",
            app_version="1.0.0",
            connection_retries=5,
            retry_delay=1,
            auto_reconnect=True,
        )
        await self.client.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str):
        if not self.client:
            raise RuntimeError("Client not connected")
        return await self.client.send_code_request(phone)

    async def sign_in(self, phone: str, code: str):
        if not self.client:
            raise RuntimeError("Client not connected")
        try:
            return await self.client.sign_in(phone, code)
        except SessionPasswordNeededError:
            raise

    async def sign_in_with_password(self, password: str):
        if not self.client:
            raise RuntimeError("Client not connected")
        return await self.client.sign_in(password=password)

    async def get_me(self):
        if not self.client:
            raise RuntimeError("Client not connected")
        return await self.client.get_me()

    async def get_channels(self) -> list[dict]:
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

    # ── Download engine ────────────────────────────────────────────────

    async def download_all_channels(self, channels: list[dict], download_dir: str) -> dict:
        if not self.client:
            raise RuntimeError("Client not connected")

        self._bytes_downloaded = 0
        self._bytes_total = 0
        self._active_count = 0
        self._speed_start = time.monotonic()

        chan_sem = asyncio.Semaphore(self.concurrent_channels)
        aggregated = {"downloaded": 0, "skipped": 0, "errors": 0, "total": 0}

        async def process_one(ch: dict) -> dict:
            if self._cancel_flag:
                return {"downloaded": 0, "skipped": 0, "errors": 0, "total": 0}
            async with chan_sem:
                return await self._download_channel(ch["id"], ch["title"], download_dir)

        tasks = [asyncio.create_task(process_one(ch)) for ch in channels]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                self._log(f"Channel '{channels[i]['title']}' failed: {r}")
                aggregated["errors"] += 1
            elif isinstance(r, dict):
                for k in aggregated:
                    aggregated[k] += r.get(k, 0)

        return aggregated

    async def _download_channel(self, channel_id: int, channel_title: str, download_dir: str) -> dict:
        channel_dir = Path(download_dir) / self._safe_name(channel_title)
        channel_dir.mkdir(parents=True, exist_ok=True)

        stats = {"downloaded": 0, "skipped": 0, "errors": 0, "total": 0}

        self._log(f"Scanning '{channel_title}'...")
        entity = await self.client.get_entity(channel_id)

        # Parse date filters
        date_from = None
        date_to = None
        if self.config.date_from:
            try:
                date_from = datetime.strptime(self.config.date_from, "%Y-%m-%d")
            except ValueError:
                pass
        if self.config.date_to:
            try:
                date_to = datetime.strptime(self.config.date_to, "%Y-%m-%d")
                date_to = date_to.replace(hour=23, minute=59, second=59)
            except ValueError:
                pass

        media_types = self.config.media_types  # "all", "photos", "videos"

        jobs = []
        async for message in self.client.iter_messages(entity, wait_time=0.3):
            if self._cancel_flag:
                break

            # Date filter
            msg_date = message.date.replace(tzinfo=None)
            if date_from and msg_date < date_from:
                continue
            if date_to and msg_date > date_to:
                continue

            job = self._classify_message(message, channel_id, channel_dir, media_types)
            if job is None:
                continue

            stats["total"] += 1
            if self.db.is_downloaded(job.file_id) or job.filepath.exists():
                stats["skipped"] += 1
                continue
            jobs.append(job)

        self._log(
            f"'{channel_title}': {stats['total']} media, "
            f"{len(jobs)} new ({stats['skipped']} already have)"
        )

        if not jobs:
            return stats

        self._bytes_total += sum(j.estimated_size for j in jobs)
        sem = asyncio.Semaphore(self.concurrent_downloads)

        async def download_one(job: DownloadJob) -> bool:
            if self._cancel_flag:
                return False
            async with sem:
                with self._speed_lock:
                    self._active_count += 1
                self._emit_speed()
                try:
                    message = await self.client.get_messages(job.channel_id, ids=job.message_id)
                    if not message or not message.media:
                        self._emit_speed(just_finished=0)
                        return False
                    path = await self.client.download_media(message, str(job.filepath))
                    if path:
                        actual_size = job.filepath.stat().st_size if job.filepath.exists() else job.estimated_size
                        self.db.mark_downloaded(job.file_id, job.channel_id, job.filename, actual_size)
                        self._emit_speed(just_finished=actual_size)
                        self._log(f"  OK {job.filename} ({self._fmt_size(actual_size)})")
                        return True
                    else:
                        self._emit_speed(just_finished=0)
                        return False
                except FloodWaitError as e:
                    self._log(f"Rate limited - waiting {e.seconds}s...")
                    await asyncio.sleep(e.seconds + 1)
                    try:
                        message = await self.client.get_messages(job.channel_id, ids=job.message_id)
                        if message and message.media:
                            path = await self.client.download_media(message, str(job.filepath))
                            if path:
                                actual_size = job.filepath.stat().st_size if job.filepath.exists() else job.estimated_size
                                self.db.mark_downloaded(job.file_id, job.channel_id, job.filename, actual_size)
                                self._emit_speed(just_finished=actual_size)
                                self._log(f"  OK {job.filename} ({self._fmt_size(actual_size)})")
                                return True
                    except Exception:
                        pass
                    self._emit_speed(just_finished=0)
                    return False
                except Exception as e:
                    self._log(f"Error: {job.filename} - {e}")
                    self._emit_speed(just_finished=0)
                    return False

        tasks = [asyncio.create_task(download_one(job)) for job in jobs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if r is True:
                stats["downloaded"] += 1
            else:
                stats["errors"] += 1

        self._log(
            f"'{channel_title}' done: {stats['downloaded']} dl, "
            f"{stats['skipped']} skip, {stats['errors']} err"
        )
        return stats

    def _classify_message(self, message, channel_id: int, channel_dir: Path, media_types: str = "all") -> DownloadJob | None:
        media = message.media
        if not media:
            return None

        file_id = None
        ext = ""
        estimated_size = 0

        if isinstance(media, MessageMediaPhoto):
            if media_types == "videos":
                return None
            file_id = str(message.id)
            ext = ".jpg"
            if hasattr(media, "photo") and media.photo:
                sizes = getattr(media.photo, "sizes", [])
                for size in sizes:
                    sz = getattr(size, "size", 0)
                    if isinstance(sz, int) and sz > estimated_size:
                        estimated_size = sz
            if estimated_size == 0:
                estimated_size = 500_000

        elif isinstance(media, MessageMediaDocument):
            doc = media.document
            if not doc:
                return None
            mime = doc.mime_type or ""
            is_video = mime.startswith("video/") or "video" in mime
            is_gif = mime == "image/gif"
            if not (is_video or is_gif):
                return None
            if media_types == "photos":
                return None
            file_id = str(doc.id)
            estimated_size = getattr(doc, "size", 0) or 5_000_000
            for attr in doc.attributes:
                name = getattr(attr, "file_name", None)
                if name:
                    ext = Path(name).suffix.lower()
                    break
            if not ext:
                ext = ".mp4" if is_video else ".gif"
        else:
            return None

        if not file_id:
            return None

        timestamp = message.date.strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{message.id}{ext}"

        return DownloadJob(
            message_id=message.id,
            file_id=file_id,
            filename=filename,
            filepath=channel_dir / filename,
            channel_id=channel_id,
            estimated_size=estimated_size,
        )

    @staticmethod
    def _safe_name(name: str) -> str:
        invalid = '<>:"/\\|?*'
        for c in invalid:
            name = name.replace(c, "_")
        return name.strip()[:100]

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.1f} GB"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f} MB"
        if n >= 1_000:
            return f"{n / 1_000:.0f} KB"
        return f"{n} B"

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
            self.client = None
