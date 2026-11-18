"""
Media browser with thumbnail grid for previewing and selecting files to download.
"""
import asyncio
import io
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from PIL import Image, ImageTk, ImageDraw

from core import TelegramEngine

THUMB_SIZE = 180
THUMB_PAD = 6
CELL_WIDTH = THUMB_SIZE + 16
CELL_HEIGHT = THUMB_SIZE + 72
PARALLEL_THUMBS = 8

BG = "#1a1a2e"
BG2 = "#16213e"
BG3 = "#0f3460"
FG = "#e0e0e0"
FG_DIM = "#8899aa"
ACCENT = "#00a8cc"
ACCENT2 = "#e94560"
LIST_BG = "#0d1b2a"


class MediaItem:
    def __init__(self, job, message_date, is_video=False, thumb_bytes=None):
        self.job = job
        self.message_date = message_date
        self.is_video = is_video
        self.thumb_bytes = thumb_bytes
        self.selected = tk.BooleanVar(value=True)
        self.thumb_image = None


class MediaBrowser(tk.Toplevel):
    """Thumbnail grid browser for selecting media to download."""

    def __init__(self, parent, engine: TelegramEngine, channels: list[dict],
                 download_dir: str, loop):
        super().__init__(parent)
        self.engine = engine
        self.channels = channels
        self.download_dir = download_dir
        self.loop = loop
        self.items: list[MediaItem] = []
        self._all_items: list[MediaItem] = []
        self._cancelled = False
        self._cols = 4
        self._placeholder_img = None
        self._video_overlay = None

        self.title("Browse Media")
        self.geometry("1150x780")
        self.minsize(700, 450)
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._center_on_parent(parent)
        self.after(100, self._start_loading)
        self.bind("<Configure>", self._on_resize)

    def _center_on_parent(self, parent):
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

    def _on_resize(self, event):
        if event.widget == self:
            new_cols = max(1, (event.width - 20) // (CELL_WIDTH + THUMB_PAD))
            if new_cols != self._cols:
                self._cols = new_cols
                if self._all_items:
                    self._filter_and_render()

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=BG3, height=48)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        self._title_label = tk.Label(top, text="Loading media...", bg=BG3,
                                     fg=ACCENT, font=("Segoe UI", 11, "bold"))
        self._title_label.pack(side=tk.LEFT, padx=14, pady=12)

        # Search box
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *a: self._filter_and_render())
        search_frame = tk.Frame(top, bg=BG3)
        search_frame.pack(side=tk.RIGHT, padx=14, pady=10)
        tk.Label(search_frame, text="\U0001F50D", bg=BG3, fg=FG_DIM,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 4))
        self._search_entry = tk.Entry(search_frame, textvariable=self._search_var,
                                      bg=LIST_BG, fg=FG, insertbackground=FG,
                                      font=("Segoe UI", 9), width=22, relief=tk.FLAT)
        self._search_entry.pack(side=tk.LEFT, ipady=3)

        # Sort
        self._sort_var = tk.StringVar(value="Newest first")
        sort_opts = ["Newest first", "Oldest first", "Largest first", "Smallest first"]
        self._sort_menu = tk.OptionMenu(top, self._sort_var, *sort_opts,
                                        command=lambda _: self._filter_and_render())
        self._sort_menu.configure(bg=BG3, fg=FG, font=("Segoe UI", 8),
                                  activebackground=BG2, activeforeground=FG,
                                  highlightthickness=0, borderwidth=0)
        self._sort_menu["menu"].configure(bg=BG2, fg=FG, font=("Segoe UI", 8),
                                          activebackground=ACCENT)
        self._sort_menu.pack(side=tk.RIGHT, padx=(0, 8), pady=10)

        # Canvas
        self._canvas = tk.Canvas(self, bg=LIST_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._inner = tk.Frame(self._canvas, bg=LIST_BG)
        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))
        self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel
        self._canvas.bind("<Enter>",
                          lambda e: self._canvas.bind_all(
                              "<MouseWheel>",
                              lambda e: self._canvas.yview_scroll(
                                  int(-1 * (e.delta / 120)), "units")))
        self._canvas.bind("<Leave>",
                          lambda e: self._canvas.unbind_all("<MouseWheel>"))

        # Bottom bar
        bottom = tk.Frame(self, bg=BG3, height=52)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        bottom.pack_propagate(False)

        for text, cmd in [("Select All", self._select_all),
                          ("Deselect All", self._deselect_all),
                          ("Invert", self._invert_selection)]:
            btn = tk.Button(bottom, text=text, bg=BG3, fg=FG if text != "Invert" else FG_DIM,
                            font=("Segoe UI", 9 if text != "Invert" else 8), relief=tk.FLAT,
                            activebackground=ACCENT, activeforeground="#fff",
                            command=cmd, cursor="hand2")
            btn.pack(side=tk.LEFT, padx=(14 if text == "Select All" else 4, 4), pady=12)

        self._selected_label = tk.Label(bottom, text="", bg=BG3, fg=FG_DIM,
                                        font=("Segoe UI", 9))
        self._selected_label.pack(side=tk.LEFT, padx=14, pady=12)

        self._dl_btn = tk.Button(bottom, text="Download Selected (0)", bg=ACCENT,
                                 fg="#fff", font=("Segoe UI", 10, "bold"),
                                 relief=tk.FLAT, activebackground="#0099bb",
                                 activeforeground="#fff", command=self._on_download,
                                 state=tk.DISABLED, cursor="hand2")
        self._dl_btn.pack(side=tk.RIGHT, padx=(0, 14), pady=10)

        cancel_btn = tk.Button(bottom, text="Cancel", bg=BG3, fg=FG,
                               font=("Segoe UI", 9), relief=tk.FLAT,
                               activebackground=ACCENT2, activeforeground="#fff",
                               command=self.destroy, cursor="hand2")
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 8), pady=10)

    def _start_loading(self):
        asyncio.run_coroutine_threadsafe(self._load_media(), self.loop)

    async def _load_media(self):
        """Scan channels then download thumbnails in parallel."""
        all_items = []

        def _update_title(text):
            self.after(0, lambda t=text: self._title_label.config(text=t))

        # Phase 1: Scan
        from telethon.tl.types import MessageMediaDocument as MMD
        for ch in self.channels:
            _update_title(f"Scanning: {ch['title']}...")
            channel_dir = Path(self.download_dir) / self.engine._safe_name(ch['title'])
            channel_dir.mkdir(parents=True, exist_ok=True)
            try:
                entity = await self.engine.client.get_entity(ch['id'])
                async for message in self.engine.client.iter_messages(entity, wait_time=0.2):
                    if self._cancelled:
                        return
                    job = self.engine._classify_message(message, ch['id'], channel_dir)
                    if job is None:
                        continue
                    is_video = isinstance(message.media, MMD)
                    all_items.append(MediaItem(job, message.date, is_video=is_video))
            except Exception as e:
                self.after(0, lambda e=e: messagebox.showerror(
                    "Error", f"Failed to scan {ch['title']}: {e}", parent=self))

        self._all_items = all_items
        _update_title(f"Media Browser - {len(all_items)} items")

        # Phase 2: Download thumbnails in parallel
        if all_items:
            sem = asyncio.Semaphore(PARALLEL_THUMBS)
            loaded = [0]
            total = len(all_items)

            async def load_thumb(item: MediaItem):
                async with sem:
                    try:
                        message = await self.engine.client.get_messages(
                            item.job.channel_id, ids=item.job.message_id)
                        if message:
                            item.thumb_bytes = await self._get_thumb_smart(message)
                    except Exception:
                        pass
                    loaded[0] += 1
                    if loaded[0] % 25 == 0 or loaded[0] == total:
                        _update_title(f"Loading thumbnails... ({loaded[0]}/{total})")

            tasks = [asyncio.create_task(load_thumb(item)) for item in all_items]
            await asyncio.gather(*tasks, return_exceptions=True)

        self.after(0, self._render_grid)

    async def _get_thumb_smart(self, message) -> bytes | None:
        """Get smallest available thumbnail bytes."""
        try:
            if not message.media:
                return None
            # Try smallest first
            data = await self.engine.client.download_media(message, file=bytes, thumb=0)
            if data and isinstance(data, bytes) and len(data) > 100:
                if len(data) < 500:
                    # Too tiny, try next size
                    data2 = await self.engine.client.download_media(message, file=bytes, thumb=1)
                    if data2 and isinstance(data2, bytes) and len(data2) > 500:
                        return data2
                return data
            # Fallback
            data = await self.engine.client.download_media(message, file=bytes, thumb=1)
            if data and isinstance(data, bytes) and len(data) > 100:
                return data
        except Exception:
            pass
        return None

    def _filter_and_render(self):
        if not self._all_items:
            return
        self._apply_filter()
        self._render_grid()

    def _apply_filter(self):
        query = self._search_var.get().lower().strip()
        if query:
            self.items = [it for it in self._all_items
                          if query in it.job.filename.lower()]
        else:
            self.items = list(self._all_items)

        sort = self._sort_var.get()
        if sort == "Newest first":
            self.items.sort(key=lambda it: it.message_date, reverse=True)
        elif sort == "Oldest first":
            self.items.sort(key=lambda it: it.message_date)
        elif sort == "Largest first":
            self.items.sort(key=lambda it: it.job.estimated_size, reverse=True)
        elif sort == "Smallest first":
            self.items.sort(key=lambda it: it.job.estimated_size)

    def _render_grid(self):
        for w in self._inner.winfo_children():
            w.destroy()

        if not self.items:
            self._title_label.config(text="No media found")
            self._update_counts()
            return

        self._title_label.config(
            text=f"Media Browser - {len(self.items)} shown"
            if self._search_var.get() else
            f"Media Browser - {len(self.items)} items")

        self._placeholder_img = self._create_placeholder()
        self._video_overlay = self._create_video_overlay()

        self._batch_index = 0
        self._batch_size = 24
        self._render_batch()

    def _render_batch(self):
        end = min(self._batch_index + self._batch_size, len(self.items))

        for i in range(self._batch_index, end):
            item = self.items[i]
            row = i // self._cols
            col = i % self._cols

            cell = tk.Frame(self._inner, bg=BG2, highlightbackground=BG3,
                            highlightthickness=1, width=CELL_WIDTH,
                            height=CELL_HEIGHT, cursor="hand2")
            cell.grid(row=row, column=col, padx=THUMB_PAD // 2,
                      pady=THUMB_PAD // 2)
            cell.grid_propagate(False)

            # Thumbnail image
            if item.thumb_bytes:
                try:
                    img = Image.open(io.BytesIO(item.thumb_bytes))
                    img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
                    item.thumb_image = ImageTk.PhotoImage(img)
                except Exception:
                    item.thumb_image = self._placeholder_img
            else:
                item.thumb_image = self._placeholder_img

            thumb_frame = tk.Frame(cell, bg=BG2, width=THUMB_SIZE, height=THUMB_SIZE)
            thumb_frame.pack(pady=(4, 0))
            thumb_frame.pack_propagate(False)

            thumb_label = tk.Label(thumb_frame, image=item.thumb_image,
                                   bg="#000000", cursor="hand2")
            thumb_label.place(x=0, y=0, width=THUMB_SIZE, height=THUMB_SIZE)
            thumb_label.image = item.thumb_image

            # Video play overlay
            vid_overlay = None
            if item.is_video:
                vid_overlay = tk.Label(thumb_frame, image=self._video_overlay,
                                       bg="#000000", cursor="hand2")
                vid_overlay.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
                vid_overlay.image = self._video_overlay

            # Click thumbnail to toggle
            for w in [thumb_label, cell] + ([vid_overlay] if vid_overlay else []):
                w.bind("<Button-1>", lambda e, it=item: self._toggle_item(it))

            # Filename
            name = item.job.filename
            if len(name) > 24:
                name = name[:11] + "\u2026" + name[-10:]
            tk.Label(cell, text=name, bg=BG2, fg=FG,
                     font=("Segoe UI", 7), anchor=tk.CENTER).pack(
                fill=tk.X, padx=4, pady=(2, 0))

            # Size + date
            size_str = self.engine._fmt_size(item.job.estimated_size)
            date_str = item.message_date.strftime("%m/%d/%y")
            tk.Label(cell, text=f"{size_str}  \u2022  {date_str}",
                     bg=BG2, fg=FG_DIM, font=("Segoe UI", 7)).pack(
                fill=tk.X, padx=4)

            # Checkbox
            tk.Checkbutton(cell, text="Download", variable=item.selected,
                           bg=BG2, fg=FG, selectcolor=BG3,
                           activebackground=BG2, activeforeground=FG,
                           font=("Segoe UI", 8),
                           command=self._update_counts).pack(pady=(1, 2))

        self._batch_index = end
        if self._batch_index < len(self.items):
            self.after(5, self._render_batch)
        else:
            self._update_counts()
            self._dl_btn.config(state=tk.NORMAL)

    def _toggle_item(self, item):
        item.selected.set(not item.selected.get())
        self._update_counts()

    def _create_placeholder(self):
        img = Image.new("RGB", (THUMB_SIZE, THUMB_SIZE), (10, 20, 35))
        draw = ImageDraw.Draw(img)
        cx, cy = THUMB_SIZE // 2, THUMB_SIZE // 2
        r = THUMB_SIZE // 6
        bw, bh = THUMB_SIZE // 3, THUMB_SIZE // 4
        draw.rounded_rectangle(
            [cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2],
            radius=4, outline="#334466", width=2)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#334466", width=2)
        draw.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2],
                     fill="#334466")
        return ImageTk.PhotoImage(img)

    def _create_video_overlay(self):
        size = 48
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([2, 2, size - 2, size - 2],
                     fill=(0, 0, 0, 140), outline=(255, 255, 255, 100), width=2)
        tri = size // 3
        cx, cy = size // 2 + 2, size // 2
        draw.polygon([
            (cx - tri // 3, cy - tri // 2),
            (cx - tri // 3, cy + tri // 2),
            (cx + tri // 2, cy),
        ], fill=(255, 255, 255, 230))
        return ImageTk.PhotoImage(img)

    def _select_all(self):
        for item in self.items:
            item.selected.set(True)
        self._update_counts()

    def _deselect_all(self):
        for item in self.items:
            item.selected.set(False)
        self._update_counts()

    def _invert_selection(self):
        for item in self.items:
            item.selected.set(not item.selected.get())
        self._update_counts()

    def _update_counts(self):
        if not self.items:
            return
        selected = sum(1 for it in self.items if it.selected.get())
        total = len(self.items)
        total_size = sum(it.job.estimated_size for it in self.items if it.selected.get())
        size_str = self.engine._fmt_size(total_size)
        self._selected_label.config(text=f"{selected} / {total} selected  \u2022  {size_str}")
        self._dl_btn.config(text=f"Download Selected ({selected})")
        self._dl_btn.config(state=tk.NORMAL if selected > 0 else tk.DISABLED)

    def _on_download(self):
        selected_jobs = [it.job for it in self.items if it.selected.get()]
        if not selected_jobs:
            return
        count = len(selected_jobs)
        total_size = sum(j.estimated_size for j in selected_jobs)
        if not messagebox.askyesno("Confirm",
                                   f"Download {count} files ({self.engine._fmt_size(total_size)})?",
                                   parent=self):
            return
        self._selected_jobs = selected_jobs
        self.destroy()

    def get_selected_jobs(self):
        return getattr(self, '_selected_jobs', None)
