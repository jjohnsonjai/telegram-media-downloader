"""
Core Telegram engine using Telethon.
High-speed parallel downloads with chunked file splitting for large media.
"""
import sqlite3
import asyncio
import logging
import time
import os
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
    Document,
)
from telethon.errors import SessionPasswordNeededError, FloodWaitError

from config import Config

logger = logging.getLogger(__name__)

# ── Chunked download tuning ───────────────────────────────────────────────
CHUNK_THRESHOLD = 3 * 1024 * 1024   # Files > 3 MB get chunked
CHUNK_COUNT = 8                     # Number of parallel chunks per file
CHUNK_RETRIES = 2                   # Retries per chunk


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
    document: Document | None = None
    is_large: bool = False


class TelegramEngine:
    """Core engine with chunked parallel download support."""

    DEFAULT_CONCURRENT_DOWNLOADS = 10
    DEFAULT_CONCURRENT_CHANNELS = 2
    MAX_CONCURRENT_DOWNLOADS = 16

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

        media_types = self.config.media_types

        jobs = []
        async for message in self.client.iter_messages(entity, wait_time=0.3):
            if self._cancel_flag:
                break

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
                    success, actual_size = await self._download_job(job)
                    if success:
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
                        success, actual_size = await self._download_job(job)
                        if success:
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

    async def _download_job(self, job: DownloadJob) -> tuple[bool, int]:
        """Download a single file. Uses chunked download for large files.
        Returns (success, actual_size)."""
        message = await self.client.get_messages(job.channel_id, ids=job.message_id)
        if not message or not message.media:
            return False, 0

        # Determine if chunked download is beneficial
        if job.is_large and job.document and job.estimated_size > CHUNK_THRESHOLD:
            return await self._chunked_download(job, message)

        # Single-stream download for small files and photos
        path = await self.client.download_media(message, str(job.filepath))
        if path:
            actual_size = job.filepath.stat().st_size if job.filepath.exists() else job.estimated_size
            return True, actual_size
        return False, 0

    async def _chunked_download(self, job: DownloadJob, message) -> tuple[bool, int]:
        """Download a large file in parallel chunks, then reassemble."""
        doc = job.document
        total_size = job.estimated_size
        chunk_size = total_size // CHUNK_COUNT
        if chunk_size < 512 * 1024:  # Minimum 512KB per chunk
            chunk_size = 512 * 1024

        tmp_path = job.filepath.with_suffix(job.filepath.suffix + ".tmp")

        # Calculate chunk ranges
        chunks = []
        offset = 0
        while offset < total_size:
            end = min(offset + chunk_size, total_size)
            chunks.append((offset, end - offset))
            offset = end

        # Limit to CHUNK_COUNT actual chunks
        if len(chunks) > CHUNK_COUNT:
            # Recalculate with exact CHUNK_COUNT
            chunk_size = total_size // CHUNK_COUNT
            chunks = []
            offset = 0
            for i in range(CHUNK_COUNT):
                if i == CHUNK_COUNT - 1:
                    size = total_size - offset
                else:
                    size = chunk_size
                chunks.append((offset, size))
                offset += size

        # Semaphore to limit concurrent chunks per file
        chunk_sem = asyncio.Semaphore(min(CHUNK_COUNT, 8))

        async def download_chunk(idx: int, offset: int, size: int) -> bytes | None:
            if self._cancel_flag:
                return None
            async with chunk_sem:
                for attempt in range(CHUNK_RETRIES + 1):
                    try:
                        data = b""
                        async for chunk_data in self.client.iter_download(
                            doc, offset=offset, request_size=size
                        ):
                            data += chunk_data
                            if len(data) >= size:
                                break
                        if data:
                            return data
                    except FloodWaitError as e:
                        if attempt < CHUNK_RETRIES:
                            await asyncio.sleep(e.seconds + 1)
                            continue
                        raise
                    except Exception:
                        if attempt < CHUNK_RETRIES:
                            await asyncio.sleep(1)
                            continue
                        raise
                return None

        # Download all chunks in parallel
        chunk_tasks = [
            asyncio.create_task(download_chunk(i, offset, size))
            for i, (offset, size) in enumerate(chunks)
        ]
        chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

        # Check for failures
        if any(r is None or isinstance(r, Exception) for r in chunk_results):
            # Clean up tmp
            if tmp_path.exists():
                tmp_path.unlink()
            return False, 0

        # Write chunks to temp file
        try:
            with open(tmp_path, "wb") as f:
                for i, (offset, size) in enumerate(chunks):
                    data = chunk_results[i]
                    if isinstance(data, bytes):
                        f.seek(offset)
                        f.write(data)
                    else:
                        raise ValueError(f"Chunk {i} is not bytes: {type(data)}")

            # Atomic rename
            if job.filepath.exists():
                job.filepath.unlink()
            tmp_path.rename(job.filepath)
            actual_size = job.filepath.stat().st_size
            return True, actual_size

        except Exception as e:
            self._log(f"Chunk assembly failed: {e}")
            if tmp_path.exists():
                tmp_path.unlink()
            return False, 0

    def _classify_message(self, message, channel_id: int, channel_dir: Path, media_types: str = "all") -> DownloadJob | None:
        media = message.media
        if not media:
            return None

        file_id = None
        ext = ""
        estimated_size = 0
        document = None
        is_large = False

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
            # Photos are never large enough for chunking

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
            document = doc
            is_large = estimated_size > CHUNK_THRESHOLD

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
            document=document,
            is_large=is_large,
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
