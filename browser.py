"""
Media browser with thumbnail grid for previewing and selecting files to download.
"""
import asyncio
import io
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageTk, ImageDraw

from core import TelegramEngine

# ── Thumbnail grid constants ──────────────────────────────────────────────
THUMB_SIZE = 160         # Display size of thumbnails
THUMB_COLS = 4           # Number of columns in the grid
THUMB_PAD = 8            # Padding between thumbnails
CELL_WIDTH = THUMB_SIZE + 20
CELL_HEIGHT = THUMB_SIZE + 60

# ── Color scheme (matching main GUI) ──────────────────────────────────────
BG = "#1a1a2e"
BG2 = "#16213e"
BG3 = "#0f3460"
FG = "#e0e0e0"
FG_DIM = "#8899aa"
ACCENT = "#00a8cc"
ACCENT2 = "#e94560"
GREEN = "#00c853"
LIST_BG = "#0d1b2a"


class MediaItem:
    """Represents one media file in the browser."""
    def __init__(self, job, message_date, thumb_bytes=None):
        self.job = job
        self.message_date = message_date
        self.thumb_bytes = thumb_bytes
        self.selected = tk.BooleanVar(value=True)
        self.thumb_image = None  # ImageTk.PhotoImage (set later on main thread)


class MediaBrowser(tk.Toplevel):
    """Thumbnail grid browser for selecting media to download."""

    def __init__(self, parent, engine: TelegramEngine, channels: list[dict], download_dir: str, loop):
        super().__init__(parent)
        self.engine = engine
        self.channels = channels
        self.download_dir = download_dir
        self.loop = loop
        self.items: list[MediaItem] = []
        self._loaded = False
        self._cancelled = False

        self.title("Browse Media")
        self.geometry("1100x750")
        self.minsize(800, 500)
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._center_on_parent(parent)

        # Start loading
        self.after(100, self._start_loading)

    def _center_on_parent(self, parent):
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=BG3, height=44)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        self._title_label = tk.Label(top, text="Loading media...", bg=BG3, fg=ACCENT,
                                     font=("Segoe UI", 11, "bold"))
        self._title_label.pack(side=tk.LEFT, padx=14, pady=10)

        self._count_label = tk.Label(top, text="", bg=BG3, fg=FG_DIM,
                                     font=("Segoe UI", 9))
        self._count_label.pack(side=tk.RIGHT, padx=14, pady=10)

        # Canvas with scrollbar
        self._canvas = tk.Canvas(self, bg=LIST_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._inner = tk.Frame(self._canvas, bg=LIST_BG)

        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel
        self._canvas.bind("<Enter>", lambda e: self._canvas.bind_all(
            "<MouseWheel>", lambda e: self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")))
        self._canvas.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

        # Bottom bar
        bottom = tk.Frame(self, bg=BG3, height=50)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        bottom.pack_propagate(False)

        select_all_btn = tk.Button(bottom, text="Select All", bg=BG3, fg=FG,
                                   font=("Segoe UI", 9), relief=tk.FLAT,
                                   activebackground=ACCENT, activeforeground="#fff",
                                   command=self._select_all)
        select_all_btn.pack(side=tk.LEFT, padx=(14, 6), pady=10)

        deselect_btn = tk.Button(bottom, text="Deselect All", bg=BG3, fg=FG,
                                 font=("Segoe UI", 9), relief=tk.FLAT,
                                 activebackground=ACCENT, activeforeground="#fff",
                                 command=self._deselect_all)
        deselect_btn.pack(side=tk.LEFT, padx=(0, 6), pady=10)

        self._selected_label = tk.Label(bottom, text="", bg=BG3, fg=FG_DIM,
                                        font=("Segoe UI", 9))
        self._selected_label.pack(side=tk.LEFT, padx=10, pady=10)

        self._dl_btn = tk.Button(bottom, text="Download Selected (0)", bg=ACCENT, fg="#fff",
                                 font=("Segoe UI", 10, "bold"), relief=tk.FLAT,
                                 activebackground="#0099bb", activeforeground="#fff",
                                 command=self._on_download, state=tk.DISABLED)
        self._dl_btn.pack(side=tk.RIGHT, padx=(0, 14), pady=8)

        cancel_btn = tk.Button(bottom, text="Cancel", bg=BG3, fg=FG,
                               font=("Segoe UI", 9), relief=tk.FLAT,
                               activebackground=ACCENT2, activeforeground="#fff",
                               command=self.destroy)
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 8), pady=8)

    def _start_loading(self):
        """Start async loading of media items."""
        self._title_label.config(text="Scanning channels...")
        asyncio.run_coroutine_threadsafe(self._load_media(), self.loop)

    def _run_async(self, coro, callback):
        """Schedule coroutine using the engine's approach."""
        # Use the same loop the engine uses
        loop = asyncio.get_event_loop()
        future = asyncio.ensure_future(coro, loop=loop)

        def check():
            if future.done():
                try:
                    result = future.result()
                    self.after(0, callback, result)
                except Exception as e:
                    self.after(0, callback, None)
                    self.after(0, lambda: messagebox.showerror("Error", str(e), parent=self))
            else:
                self.after(100, check)

        self.after(100, check)

    async def _load_media(self):
        """Scan channels and collect all media items with thumbnails."""
        all_items = []

        # Helper to schedule UI-safe label update
        def _update_title(text):
            self.after(0, lambda: self._title_label.config(text=text))

        for ch in self.channels:
            _update_title(f"Scanning: {ch['title']}...")

            channel_dir = Path(self.download_dir) / self.engine._safe_name(ch['title'])
            channel_dir.mkdir(parents=True, exist_ok=True)

            try:
                entity = await self.engine.client.get_entity(ch['id'])
                async for message in self.engine.client.iter_messages(entity, wait_time=0.3):
                    if self._cancelled:
                        return

                    job = self.engine._classify_message(message, ch['id'], channel_dir)
                    if job is None:
                        continue

                    # Try to get thumbnail bytes
                    thumb_bytes = await self._get_thumbnail(message)

                    item = MediaItem(job, message.date, thumb_bytes)
                    all_items.append(item)

            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error",
                                                           f"Failed to scan {ch['title']}: {e}", parent=self))

        self.items = all_items
        self.after(0, self._render_grid)

    async def _get_thumbnail(self, message) -> bytes | None:
        """Try to extract thumbnail bytes from a message."""
        try:
            media = message.media
            if not media:
                return None

            # Try downloading just the thumbnail
            thumb_data = await self.engine.client.download_media(
                message, file=bytes, thumb=1  # -1 = biggest thumb, 0 = small
            )
            if thumb_data and isinstance(thumb_data, bytes) and len(thumb_data) > 100:
                return thumb_data

            # Fallback: try thumb=0 (smallest)
            thumb_data = await self.engine.client.download_media(
                message, file=bytes, thumb=0
            )
            if thumb_data and isinstance(thumb_data, bytes) and len(thumb_data) > 100:
                return thumb_data

        except Exception:
            pass
        return None

    def _render_grid(self):
        """Render all thumbnails in the grid."""
        if not self.items:
            self._title_label.config(text="No media found")
            return

        self._title_label.config(
            text=f"Media Browser — {len(self.items)} items"
        )
        self._update_counts()

        # Clear inner frame
        for w in self._inner.winfo_children():
            w.destroy()

        # Create placeholder image for items without thumbnails
        self._placeholder = self._create_placeholder()

        # Render in batches to keep UI responsive
        self._batch_size = 20
        self._batch_index = 0
        self._render_batch()

    def _render_batch(self):
        """Render next batch of thumbnails."""
        end = min(self._batch_index + self._batch_size, len(self.items))
        cols = max(1, (self.winfo_width() - 20) // (CELL_WIDTH + THUMB_PAD))

        for i in range(self._batch_index, end):
            item = self.items[i]
            row = i // cols
            col = i % cols

            # Cell frame
            cell = tk.Frame(self._inner, bg=BG2, highlightbackground=BG3,
                            highlightthickness=1, width=CELL_WIDTH, height=CELL_HEIGHT)
            cell.grid(row=row, column=col, padx=THUMB_PAD // 2, pady=THUMB_PAD // 2)
            cell.grid_propagate(False)

            # Thumbnail
            if item.thumb_bytes:
                try:
                    img = Image.open(io.BytesIO(item.thumb_bytes))
                    img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
                    item.thumb_image = ImageTk.PhotoImage(img)
                except Exception:
                    item.thumb_image = self._placeholder
            else:
                item.thumb_image = self._placeholder

            thumb_label = tk.Label(cell, image=item.thumb_image, bg=BG2, cursor="hand2")
            thumb_label.pack(pady=(4, 2))
            # Keep reference
            thumb_label.image = item.thumb_image

            # Filename (truncated)
            name = item.job.filename
            if len(name) > 22:
                name = name[:10] + "..." + name[-9:]
            name_label = tk.Label(cell, text=name, bg=BG2, fg=FG,
                                  font=("Segoe UI", 7), anchor=tk.CENTER)
            name_label.pack(fill=tk.X, padx=4)

            # Size + date
            size_str = self.engine._fmt_size(item.job.estimated_size)
            date_str = item.message_date.strftime("%m/%d")
            info_label = tk.Label(cell, text=f"{size_str}  •  {date_str}",
                                  bg=BG2, fg=FG_DIM, font=("Segoe UI", 7))
            info_label.pack(fill=tk.X, padx=4)

            # Checkbox
            cb = tk.Checkbutton(cell, text="Download", variable=item.selected,
                                bg=BG2, fg=FG, selectcolor=BG3,
                                activebackground=BG2, activeforeground=FG,
                                font=("Segoe UI", 8),
                                command=self._update_counts)
            cb.pack(pady=(1, 2))

        self._batch_index = end

        if self._batch_index < len(self.items):
            self.after(10, self._render_batch)
        else:
            self._update_counts()
            self._dl_btn.config(state=tk.NORMAL)

    def _create_placeholder(self):
        """Create a placeholder thumbnail image."""
        img = Image.new("RGB", (THUMB_SIZE, THUMB_SIZE), (13, 27, 42))
        draw = ImageDraw.Draw(img)
        # Draw a simple camera/media icon
        cx, cy = THUMB_SIZE // 2, THUMB_SIZE // 2
        r = THUMB_SIZE // 5
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#334455", width=2)
        draw.line([cx + r, cy + r, cx + r * 2, cy + r * 2], fill="#334455", width=2)
        return ImageTk.PhotoImage(img)

    def _select_all(self):
        for item in self.items:
            item.selected.set(True)
        self._update_counts()

    def _deselect_all(self):
        for item in self.items:
            item.selected.set(False)
        self._update_counts()

    def _update_counts(self):
        if not self.items:
            return
        selected = sum(1 for item in self.items if item.selected.get())
        total = len(self.items)
        total_size = sum(item.job.estimated_size for item in self.items if item.selected.get())
        size_str = self.engine._fmt_size(total_size)
        self._selected_label.config(text=f"{selected} / {total} selected  •  {size_str}")
        self._dl_btn.config(text=f"Download Selected ({selected})")
        if selected > 0:
            self._dl_btn.config(state=tk.NORMAL)
        else:
            self._dl_btn.config(state=tk.DISABLED)

    def _on_download(self):
        """Return selected items to caller for download."""
        selected_jobs = [item.job for item in self.items if item.selected.get()]
        if not selected_jobs:
            return

        count = len(selected_jobs)
        total_size = sum(j.estimated_size for j in selected_jobs)
        size_str = self.engine._fmt_size(total_size)

        if not messagebox.askyesno("Confirm",
                                   f"Download {count} files ({size_str})?",
                                   parent=self):
            return

        self._selected_jobs = selected_jobs
        self._confirmed = True
        self.destroy()

    def get_selected_jobs(self):
        """After window closes, returns list of selected DownloadJobs or None if cancelled."""
        return getattr(self, '_selected_jobs', None)
