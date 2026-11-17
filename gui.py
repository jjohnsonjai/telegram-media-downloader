"""
Modern tabbed GUI for Telegram Media Downloader.
Dark theme, system tray, download queue, channel search.
"""
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from datetime import datetime

from config import Config
from core import TelegramEngine


# ── Color scheme ──────────────────────────────────────────────────────────

THEME = {
    "bg": "#1a1a2e",
    "bg2": "#16213e",
    "bg3": "#0f3460",
    "fg": "#e0e0e0",
    "fg_dim": "#8899aa",
    "accent": "#00a8cc",
    "accent2": "#e94560",
    "green": "#00c853",
    "orange": "#ff9100",
    "red": "#ff1744",
    "entry_bg": "#0d1b2a",
    "list_bg": "#0d1b2a",
    "log_bg": "#0a0a1a",
    "border": "#1b3a5c",
}


class PlaceholderEntry(ttk.Entry):
    """Entry widget with placeholder text."""
    def __init__(self, parent, placeholder="", show=None, **kwargs):
        self._placeholder = placeholder
        self._has_placeholder = False
        super().__init__(parent, **kwargs)
        if show:
            self.configure(show=show)
        self._show = show
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._on_focus_out()

    def _on_focus_in(self, e=None):
        if self._has_placeholder:
            self.delete(0, tk.END)
            if self._show:
                self.configure(show=self._show)
            self._has_placeholder = False

    def _on_focus_out(self, e=None):
        if not self.get():
            self._has_placeholder = True
            if self._show:
                self.configure(show="")
            self.insert(0, self._placeholder)

    def get(self):
        val = super().get()
        return "" if self._has_placeholder else val


class TelegramDownloaderGUI:
    def __init__(self):
        self.config = Config()
        self.engine = TelegramEngine(self.config)
        self.engine.concurrent_downloads = self.config.concurrent_downloads
        self.engine.concurrent_channels = self.config.concurrent_channels

        self.loop = asyncio.new_event_loop()
        self._running = False
        self._channels = []
        self._download_active = False
        self._speed_timer = None

        # Build root
        self.root = tk.Tk()
        self.root.title("Telegram Media Downloader")
        self.root.geometry("860x680")
        self.root.minsize(700, 500)
        self.root.configure(bg=THEME["bg"])

        self._setup_theme()
        self._build_ui()
        self._center_window()
        self._start_async_thread()

        # System tray
        self._setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Theme ──────────────────────────────────────────────────────────

    def _setup_theme(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Configure styles
        style.configure(".", background=THEME["bg"], foreground=THEME["fg"])
        style.configure("TFrame", background=THEME["bg"])
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["fg"])
        style.configure("TLabelframe", background=THEME["bg"], foreground=THEME["fg"])
        style.configure("TLabelframe.Label", background=THEME["bg"], foreground=THEME["accent"])
        style.configure("TButton", background=THEME["bg3"], foreground=THEME["fg"],
                        borderwidth=1, focusthickness=0, padding=(12, 6))
        style.map("TButton", background=[("active", THEME["accent"]), ("disabled", THEME["bg2"])],
                  foreground=[("disabled", THEME["fg_dim"])])
        style.configure("Accent.TButton", background=THEME["accent"], foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#0099bb"), ("disabled", THEME["bg2"])])
        style.configure("Danger.TButton", background=THEME["accent2"], foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#c0392b")])
        style.configure("TEntry", fieldbackground=THEME["entry_bg"], foreground=THEME["fg"],
                        borderwidth=1)
        style.configure("TSpinbox", fieldbackground=THEME["entry_bg"], foreground=THEME["fg"])
        style.configure("TNotebook", background=THEME["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=THEME["bg2"], foreground=THEME["fg"],
                        padding=(16, 8), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", THEME["bg3"])],
                  foreground=[("selected", THEME["accent"])])
        style.configure("TCombobox", fieldbackground=THEME["entry_bg"], foreground=THEME["fg"])
        style.configure("TCheckbutton", background=THEME["bg"], foreground=THEME["fg"])
        style.map("TCheckbutton", background=[("active", THEME["bg"])])
        style.configure("TProgressbar", background=THEME["accent"], troughcolor=THEME["bg2"],
                        borderwidth=0, thickness=6)
        style.configure("Vertical.TScrollbar", background=THEME["bg3"], troughcolor=THEME["bg2"],
                        arrowcolor=THEME["fg"], borderwidth=0)

    # ── System tray ────────────────────────────────────────────────────

    def _setup_tray(self):
        try:
            import pystray
            from PIL import Image
            icon_path = Path(__file__).parent / "icon.png"
            if icon_path.exists():
                img = Image.open(icon_path)
                self._tray_icon = pystray.Icon(
                    "telegram_downloader", img, "Telegram Media Downloader",
                    menu=pystray.Menu(
                        pystray.MenuItem("Show", self._tray_show, default=True),
                        pystray.MenuItem("Exit", self._tray_exit),
                    )
                )
                self._has_tray = True
            else:
                self._has_tray = False
        except ImportError:
            self._has_tray = False

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self._restore_window)

    def _tray_exit(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
        self.root.after(0, self._force_close)

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _minimize_to_tray(self):
        if self._has_tray and self.config.minimize_to_tray:
            self.root.withdraw()
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
        else:
            self.root.iconify()

    def _force_close(self):
        self._running = False
        if self.engine:
            try:
                future = asyncio.run_coroutine_threadsafe(self.engine.disconnect(), self.loop)
                future.result(timeout=3)
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()

    # ── Window management ──────────────────────────────────────────────

    def _center_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _start_async_thread(self):
        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
        t = threading.Thread(target=run_loop, daemon=True)
        t.start()

    def _run_async(self, coro, callback=None):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        def check():
            if future.done():
                try:
                    result = future.result()
                    if callback:
                        self.root.after(0, callback, result)
                except Exception as e:
                    if callback:
                        self.root.after(0, callback, None)
                    self.root.after(0, self._show_error, str(e))
            else:
                self.root.after(100, check)
        self.root.after(100, check)

    # ── UI Build ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Main container
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True)

        # Header bar
        header = tk.Frame(main, bg=THEME["bg3"], height=48)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text="  Telegram Media Downloader", bg=THEME["bg3"],
                 fg=THEME["accent"], font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT, pady=10)

        # Status indicator
        self._status_dot = tk.Canvas(header, width=12, height=12, bg=THEME["bg3"],
                                     highlightthickness=0)
        self._status_dot.pack(side=tk.RIGHT, padx=(0, 12), pady=16)
        self._draw_dot("gray")

        self._status_text = tk.Label(header, text="Disconnected", bg=THEME["bg3"],
                                     fg=THEME["fg_dim"], font=("Segoe UI", 9))
        self._status_text.pack(side=tk.RIGHT, padx=(0, 6))

        # Tab bar
        self._notebook = ttk.Notebook(main)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))

        self._build_connect_tab()
        self._build_channels_tab()
        self._build_downloads_tab()
        self._build_settings_tab()

        # Status bar
        status_bar = tk.Frame(main, bg=THEME["bg3"], height=26)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        status_bar.pack_propagate(False)

        self._status_bar_label = tk.Label(status_bar, text="Ready", bg=THEME["bg3"],
                                          fg=THEME["fg_dim"], font=("Segoe UI", 8),
                                          anchor=tk.W)
        self._status_bar_label.pack(side=tk.LEFT, padx=10)

        self._db_stats_label = tk.Label(status_bar, text="", bg=THEME["bg3"],
                                        fg=THEME["fg_dim"], font=("Segoe UI", 8))
        self._db_stats_label.pack(side=tk.RIGHT, padx=10)
        self._update_db_stats()

    def _draw_dot(self, color):
        self._status_dot.delete("all")
        c = {"green": "#00c853", "orange": "#ff9100", "red": "#ff1744", "gray": "#555555"}.get(color, color)
        self._status_dot.create_oval(2, 2, 10, 10, fill=c, outline="")

    def _update_db_stats(self):
        count, size = self.engine.db.get_stats()
        if size > 1_000_000_000:
            s = f"{size / 1_000_000_000:.1f} GB"
        elif size > 1_000_000:
            s = f"{size / 1_000_000:.1f} MB"
        elif size > 1_000:
            s = f"{size / 1_000:.0f} KB"
        else:
            s = f"{size} B"
        self._db_stats_label.config(text=f"Downloaded: {count} files ({s})")

    # ── Tab 1: Connect ─────────────────────────────────────────────────

    def _build_connect_tab(self):
        tab = ttk.Frame(self._notebook, padding=20)
        self._notebook.add(tab, text="  Connect  ")

        # API credentials card
        card = tk.Frame(tab, bg=THEME["bg2"], highlightbackground=THEME["border"],
                        highlightthickness=1, padx=20, pady=20)
        card.pack(fill=tk.X)

        tk.Label(card, text="API Credentials", bg=THEME["bg2"], fg=THEME["accent"],
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        tk.Label(card, text="Get these from my.telegram.org/apps", bg=THEME["bg2"],
                 fg=THEME["fg_dim"], font=("Segoe UI", 8)).pack(anchor=tk.W, pady=(2, 12))

        # Grid of fields
        grid = tk.Frame(card, bg=THEME["bg2"])
        grid.pack(fill=tk.X)

        tk.Label(grid, text="API ID", bg=THEME["bg2"], fg=THEME["fg"],
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky=tk.W, pady=4)
        self.api_id_var = tk.StringVar(value=str(self.config.api_id or ""))
        self.api_id_entry = PlaceholderEntry(grid, placeholder="e.g. 12345678",
                                             textvariable=self.api_id_var, width=24)
        self.api_id_entry.grid(row=0, column=1, sticky=tk.W, padx=(12, 20), pady=4)

        tk.Label(grid, text="API Hash", bg=THEME["bg2"], fg=THEME["fg"],
                 font=("Segoe UI", 9)).grid(row=0, column=2, sticky=tk.W, pady=4)
        self.api_hash_var = tk.StringVar(value=self.config.api_hash or "")
        self.api_hash_entry = PlaceholderEntry(grid, placeholder="e.g. a1b2c3d4...",
                                               textvariable=self.api_hash_var, width=38, show="*")
        self.api_hash_entry.grid(row=0, column=3, sticky=tk.EW, padx=(12, 0), pady=4)

        tk.Label(grid, text="Phone", bg=THEME["bg2"], fg=THEME["fg"],
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky=tk.W, pady=4)
        self.phone_var = tk.StringVar(value=self.config.phone or "")
        self.phone_entry = PlaceholderEntry(grid, placeholder="+15551234567",
                                            textvariable=self.phone_var, width=24)
        self.phone_entry.grid(row=1, column=1, sticky=tk.W, padx=(12, 20), pady=4)

        self.connect_btn = ttk.Button(grid, text="Save & Connect", style="Accent.TButton",
                                      command=self._on_connect)
        self.connect_btn.grid(row=1, column=2, columnspan=2, sticky=tk.E, pady=4)

        grid.columnconfigure(3, weight=1)

        # Auth section (hidden until needed)
        self.auth_card = tk.Frame(tab, bg=THEME["bg2"], highlightbackground=THEME["border"],
                                  highlightthickness=1, padx=20, pady=15)

        tk.Label(self.auth_card, text="Verification", bg=THEME["bg2"], fg=THEME["accent"],
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        auth_grid = tk.Frame(self.auth_card, bg=THEME["bg2"])
        auth_grid.pack(fill=tk.X, pady=(10, 0))

        tk.Label(auth_grid, text="Code", bg=THEME["bg2"], fg=THEME["fg"]).grid(row=0, column=0, sticky=tk.W)
        self.code_var = tk.StringVar()
        self.code_entry = PlaceholderEntry(auth_grid, placeholder="12345",
                                           textvariable=self.code_var, width=16)
        self.code_entry.grid(row=0, column=1, sticky=tk.W, padx=10)
        self.code_btn = ttk.Button(auth_grid, text="Submit Code", command=self._on_submit_code)
        self.code_btn.grid(row=0, column=2, padx=(5, 20))

        tk.Label(auth_grid, text="2FA", bg=THEME["bg2"], fg=THEME["fg"]).grid(row=0, column=3, sticky=tk.W)
        self.pwd_var = tk.StringVar()
        self.pwd_entry = PlaceholderEntry(auth_grid, placeholder="password",
                                          textvariable=self.pwd_var, width=16, show="*")
        self.pwd_entry.grid(row=0, column=4, sticky=tk.W, padx=10)
        self.pwd_btn = ttk.Button(auth_grid, text="Submit 2FA", command=self._on_submit_password)
        self.pwd_btn.grid(row=0, column=5, padx=(5, 0))

        self.auth_msg = tk.Label(self.auth_card, text="", bg=THEME["bg2"],
                                 fg=THEME["orange"], font=("Segoe UI", 9))
        self.auth_msg.pack(anchor=tk.W, pady=(8, 0))

        # Account info card (shown after connect)
        self.acct_card = tk.Frame(tab, bg=THEME["bg2"], highlightbackground=THEME["border"],
                                  highlightthickness=1, padx=20, pady=15)

        self.acct_name_label = tk.Label(self.acct_card, text="", bg=THEME["bg2"],
                                        fg=THEME["fg"], font=("Segoe UI", 12, "bold"))
        self.acct_name_label.pack(anchor=tk.W)
        self.acct_detail_label = tk.Label(self.acct_card, text="", bg=THEME["bg2"],
                                          fg=THEME["fg_dim"], font=("Segoe UI", 9))
        self.acct_detail_label.pack(anchor=tk.W, pady=(2, 0))

        self.disconnect_btn = ttk.Button(self.acct_card, text="Disconnect",
                                         style="Danger.TButton", command=self._on_disconnect)
        self.disconnect_btn.pack(anchor=tk.W, pady=(10, 0))

    # ── Tab 2: Channels ────────────────────────────────────────────────

    def _build_channels_tab(self):
        tab = ttk.Frame(self._notebook, padding=15)
        self._notebook.add(tab, text="  Channels  ")

        # Top bar: search + refresh
        top = tk.Frame(tab, bg=THEME["bg"])
        top.pack(fill=tk.X, pady=(0, 10))

        tk.Label(top, text="", bg=THEME["bg"]).pack(side=tk.LEFT)  # spacer

        self.channel_search_var = tk.StringVar()
        self.channel_search_var.trace_add("write", lambda *a: self._filter_channels())
        search_entry = PlaceholderEntry(top, placeholder="Search channels...",
                                        textvariable=self.channel_search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(0, 10))

        self.refresh_btn = ttk.Button(top, text="↻ Refresh", command=self._on_refresh_channels)
        self.refresh_btn.pack(side=tk.LEFT)

        self.channel_count_label = tk.Label(top, text="", bg=THEME["bg"],
                                            fg=THEME["fg_dim"], font=("Segoe UI", 9))
        self.channel_count_label.pack(side=tk.RIGHT, padx=(0, 10))

        # Channel list with checkboxes
        list_frame = tk.Frame(tab, bg=THEME["bg"])
        list_frame.pack(fill=tk.BOTH, expand=True)

        # Canvas + scrollbar for channel list
        self._channel_canvas = tk.Canvas(list_frame, bg=THEME["list_bg"],
                                         highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                                  command=self._channel_canvas.yview)
        self._channel_frame = tk.Frame(self._channel_canvas, bg=THEME["list_bg"])

        self._channel_frame.bind("<Configure>",
                                 lambda e: self._channel_canvas.configure(
                                     scrollregion=self._channel_canvas.bbox("all")))

        self._channel_canvas.create_window((0, 0), window=self._channel_frame, anchor=tk.NW)
        self._channel_canvas.configure(yscrollcommand=scrollbar.set)

        self._channel_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind mousewheel
        self._channel_canvas.bind("<Enter>", lambda e: self._bind_mousewheel())
        self._channel_canvas.bind("<Leave>", lambda e: self._unbind_mousewheel())

        # Bottom bar
        bottom = tk.Frame(tab, bg=THEME["bg"])
        bottom.pack(fill=tk.X, pady=(10, 0))

        self.select_all_btn = ttk.Button(bottom, text="Select All",
                                         command=self._on_select_all)
        self.select_all_btn.pack(side=tk.LEFT)
        self.deselect_btn = ttk.Button(bottom, text="Deselect All",
                                       command=self._on_deselect_all)
        self.deselect_btn.pack(side=tk.LEFT, padx=(5, 0))

        self.selected_count_label = tk.Label(bottom, text="0 selected", bg=THEME["bg"],
                                             fg=THEME["fg_dim"], font=("Segoe UI", 9))
        self.selected_count_label.pack(side=tk.RIGHT)

        self._channel_vars = {}  # channel_id -> tk.BooleanVar
        self._channel_widgets = {}  # channel_id -> frame widget

    def _bind_mousewheel(self):
        self._channel_canvas.bind_all("<MouseWheel>",
                                      lambda e: self._channel_canvas.yview_scroll(
                                          int(-1 * (e.delta / 120)), "units"))

    def _unbind_mousewheel(self):
        self._channel_canvas.unbind_all("<MouseWheel>")

    # ── Tab 3: Downloads ───────────────────────────────────────────────

    def _build_downloads_tab(self):
        tab = ttk.Frame(self._notebook, padding=15)
        self._notebook.add(tab, text="  Downloads  ")

        # Top controls
        ctrl = tk.Frame(tab, bg=THEME["bg"])
        ctrl.pack(fill=tk.X, pady=(0, 10))

        # Directory
        tk.Label(ctrl, text="Save to:", bg=THEME["bg"], fg=THEME["fg"]).pack(side=tk.LEFT)
        self.dl_dir_var = tk.StringVar(value=self.config.download_dir)
        self.dl_dir_entry = PlaceholderEntry(ctrl, placeholder="Download folder...",
                                             textvariable=self.dl_dir_var, width=50)
        self.dl_dir_entry.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        self.browse_btn = ttk.Button(ctrl, text="Browse", command=self._on_browse_dir)
        self.browse_btn.pack(side=tk.LEFT)

        # Action buttons
        action = tk.Frame(tab, bg=THEME["bg"])
        action.pack(fill=tk.X, pady=(0, 10))

        self.download_btn = ttk.Button(action, text="▶ Start Download",
                                       style="Accent.TButton", command=self._on_download)
        self.download_btn.pack(side=tk.LEFT)

        self.cancel_btn = ttk.Button(action, text="■ Cancel", style="Danger.TButton",
                                     command=self._on_cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Speed / progress
        self.speed_frame = tk.Frame(action, bg=THEME["bg"])
        self.speed_frame.pack(side=tk.RIGHT)

        self.speed_label = tk.Label(self.speed_frame, text="", bg=THEME["bg"],
                                    fg=THEME["accent"], font=("Consolas", 10))
        self.speed_label.pack(side=tk.LEFT)

        self.progress_bar = ttk.Progressbar(action, mode="determinate", length=200)
        self.progress_bar.pack(side=tk.RIGHT, padx=(10, 0))

        # Log area
        log_frame = tk.Frame(tab, bg=THEME["log_bg"])
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, state=tk.DISABLED, bg=THEME["log_bg"],
                                fg="#aabbcc", font=("Consolas", 9),
                                insertbackground=THEME["fg"],
                                selectbackground=THEME["bg3"],
                                relief=tk.FLAT, padx=10, pady=10,
                                wrap=tk.WORD)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL,
                                   command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Clear log button
        clear_btn = ttk.Button(tab, text="Clear Log", command=self._clear_log)
        clear_btn.pack(anchor=tk.E, pady=(5, 0))

    # ── Tab 4: Settings ────────────────────────────────────────────────

    def _build_settings_tab(self):
        tab = ttk.Frame(self._notebook, padding=20)
        self._notebook.add(tab, text="  Settings  ")

        # Performance
        card1 = tk.Frame(tab, bg=THEME["bg2"], highlightbackground=THEME["border"],
                         highlightthickness=1, padx=20, pady=15)
        card1.pack(fill=tk.X, pady=(0, 12))

        tk.Label(card1, text="Performance", bg=THEME["bg2"], fg=THEME["accent"],
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        grid1 = tk.Frame(card1, bg=THEME["bg2"])
        grid1.pack(fill=tk.X, pady=(10, 0))

        tk.Label(grid1, text="Parallel file downloads", bg=THEME["bg2"],
                 fg=THEME["fg"]).grid(row=0, column=0, sticky=tk.W)
        self.settings_conc = tk.IntVar(value=self.config.concurrent_downloads)
        ttk.Spinbox(grid1, from_=1, to=16, textvariable=self.settings_conc,
                    width=4, command=self._save_settings).grid(row=0, column=1, padx=(12, 30))

        tk.Label(grid1, text="Parallel channels", bg=THEME["bg2"],
                 fg=THEME["fg"]).grid(row=0, column=2, sticky=tk.W)
        self.settings_chan = tk.IntVar(value=self.config.concurrent_channels)
        ttk.Spinbox(grid1, from_=1, to=6, textvariable=self.settings_chan,
                    width=4, command=self._save_settings).grid(row=0, column=3, padx=(12, 0))

        # Filters
        card2 = tk.Frame(tab, bg=THEME["bg2"], highlightbackground=THEME["border"],
                         highlightthickness=1, padx=20, pady=15)
        card2.pack(fill=tk.X, pady=(0, 12))

        tk.Label(card2, text="Download Filters", bg=THEME["bg2"], fg=THEME["accent"],
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        grid2 = tk.Frame(card2, bg=THEME["bg2"])
        grid2.pack(fill=tk.X, pady=(10, 0))

        tk.Label(grid2, text="Media type", bg=THEME["bg2"],
                 fg=THEME["fg"]).grid(row=0, column=0, sticky=tk.W)
        self.settings_media = ttk.Combobox(grid2, values=["all", "photos", "videos"],
                                           state="readonly", width=10)
        self.settings_media.set(self.config.media_types)
        self.settings_media.bind("<<ComboboxSelected>>", lambda e: self._save_settings())
        self.settings_media.grid(row=0, column=1, padx=(12, 0))

        # Date range
        tk.Label(grid2, text="Date from", bg=THEME["bg2"],
                 fg=THEME["fg"]).grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.settings_date_from = PlaceholderEntry(grid2, placeholder="YYYY-MM-DD (optional)",
                                                   width=18)
        if self.config.date_from:
            self.settings_date_from.delete(0, tk.END)
            self.settings_date_from.insert(0, self.config.date_from)
        self.settings_date_from.grid(row=1, column=1, padx=(12, 20), pady=(8, 0))
        self.settings_date_from.bind("<FocusOut>", lambda e: self._save_settings())

        tk.Label(grid2, text="Date to", bg=THEME["bg2"],
                 fg=THEME["fg"]).grid(row=1, column=2, sticky=tk.W, pady=(8, 0))
        self.settings_date_to = PlaceholderEntry(grid2, placeholder="YYYY-MM-DD (optional)",
                                                 width=18)
        if self.config.date_to:
            self.settings_date_to.delete(0, tk.END)
            self.settings_date_to.insert(0, self.config.date_to)
        self.settings_date_to.grid(row=1, column=3, padx=(12, 0), pady=(8, 0))
        self.settings_date_to.bind("<FocusOut>", lambda e: self._save_settings())

        # Behavior
        card3 = tk.Frame(tab, bg=THEME["bg2"], highlightbackground=THEME["border"],
                         highlightthickness=1, padx=20, pady=15)
        card3.pack(fill=tk.X, pady=(0, 12))

        tk.Label(card3, text="Behavior", bg=THEME["bg2"], fg=THEME["accent"],
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        grid3 = tk.Frame(card3, bg=THEME["bg2"])
        grid3.pack(fill=tk.X, pady=(10, 0))

        self.settings_tray = tk.BooleanVar(value=self.config.minimize_to_tray)
        cb1 = tk.Checkbutton(grid3, text="Minimize to system tray instead of closing",
                             variable=self.settings_tray, command=self._save_settings,
                             bg=THEME["bg2"], fg=THEME["fg"],
                             selectcolor=THEME["bg3"], activebackground=THEME["bg2"],
                             activeforeground=THEME["fg"])
        cb1.grid(row=0, column=0, sticky=tk.W)

        self.settings_notify = tk.BooleanVar(value=self.config.notify_on_complete)
        cb2 = tk.Checkbutton(grid3, text="Show notification when downloads complete",
                             variable=self.settings_notify, command=self._save_settings,
                             bg=THEME["bg2"], fg=THEME["fg"],
                             selectcolor=THEME["bg3"], activebackground=THEME["bg2"],
                             activeforeground=THEME["fg"])
        cb2.grid(row=1, column=0, sticky=tk.W, pady=(4, 0))

        # Danger zone
        card4 = tk.Frame(tab, bg=THEME["bg2"], highlightbackground=THEME["accent2"],
                         highlightthickness=1, padx=20, pady=15)
        card4.pack(fill=tk.X)

        tk.Label(card4, text="Danger Zone", bg=THEME["bg2"], fg=THEME["accent2"],
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        tk.Label(card4, text="Clear all saved credentials and session data",
                 bg=THEME["bg2"], fg=THEME["fg_dim"], font=("Segoe UI", 8)).pack(anchor=tk.W, pady=(2, 8))

        ttk.Button(card4, text="Clear All Data", style="Danger.TButton",
                   command=self._on_clear_data).pack(anchor=tk.W)

    def _save_settings(self):
        self.config.concurrent_downloads = self.settings_conc.get()
        self.config.concurrent_channels = self.settings_chan.get()
        self.engine.concurrent_downloads = self.config.concurrent_downloads
        self.engine.concurrent_channels = self.config.concurrent_channels

        self.config.media_types = self.settings_media.get()

        df = self.settings_date_from.get()
        self.config.date_from = df if df else None

        dt = self.settings_date_to.get()
        self.config.date_to = dt if dt else None

        self.config.minimize_to_tray = self.settings_tray.get()
        self.config.notify_on_complete = self.settings_notify.get()

        self._set_status(f"Settings saved", 2000)

    def _on_clear_data(self):
        if messagebox.askyesno("Confirm", "Delete all saved credentials, session, and download history?\n\nThis cannot be undone."):
            self.config.clear()
            session = self.config.app_dir / "session.session"
            if session.exists():
                session.unlink()
            db = self.config.app_dir / "downloads.db"
            if db.exists():
                db.unlink()
            self.api_id_var.set("")
            self.api_hash_var.set("")
            self.phone_var.set("")
            self._update_db_stats()
            self._set_status("All data cleared", 3000)

    # ── Auth flow ──────────────────────────────────────────────────────

    def _on_connect(self):
        api_id_str = self.api_id_var.get().strip()
        api_hash = self.api_hash_var.get().strip()
        phone = self.phone_var.get().strip()

        if not api_id_str or not api_hash:
            messagebox.showerror("Error", "API ID and API Hash are required.")
            return

        try:
            api_id = int(api_id_str)
        except ValueError:
            messagebox.showerror("Error", "API ID must be a number.")
            return

        self.config.api_id = api_id
        self.config.api_hash = api_hash
        if phone:
            self.config.phone = phone

        self._draw_dot("orange")
        self._status_text.config(text="Connecting...", fg=THEME["orange"])
        self._set_status("Connecting to Telegram...")
        self.connect_btn.config(state=tk.DISABLED)
        self._run_async(self._connect_and_auth(), self._on_connect_result)

    async def _connect_and_auth(self):
        connected = await self.engine.connect()
        if connected:
            me = await self.engine.get_me()
            return {"status": "connected",
                    "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                    "username": me.username or "",
                    "phone": getattr(me, "phone", "")}
        else:
            phone = self.config.phone
            if not phone:
                raise ValueError("Phone number is required for first-time login.")
            await self.engine.send_code(phone)
            return {"status": "code_sent", "phone": phone}

    def _on_connect_result(self, result):
        self.connect_btn.config(state=tk.NORMAL)
        if result is None:
            self._draw_dot("red")
            self._status_text.config(text="Connection failed", fg=THEME["red"])
            return

        status = result.get("status")
        if status == "connected":
            self._draw_dot("green")
            self._status_text.config(text="Connected", fg=THEME["green"])
            self.acct_name_label.config(text=result["name"] or "Unknown")
            detail = f"@{result['username']}" if result["username"] else ""
            if result.get("phone"):
                detail += f"  •  {result['phone']}"
            self.acct_detail_label.config(text=detail.strip())
            self.acct_card.pack(fill=tk.X, pady=(12, 0))
            self.auth_card.pack_forget()
            self._set_status(f"Connected as {result['name'] or result['username']}")
            self._on_refresh_channels()

        elif status == "code_sent":
            self.auth_msg.config(text=f"Code sent to {result['phone']}. Enter it below.")
            self.auth_card.pack(fill=tk.X, pady=(12, 0))
            self.code_entry.focus_set()

    def _on_submit_code(self):
        code = self.code_var.get().strip()
        if not code:
            return
        self.auth_msg.config(text="Verifying...", fg=THEME["orange"])
        self.code_btn.config(state=tk.DISABLED)
        self._run_async(self._sign_in(code), self._on_sign_in_result)

    async def _sign_in(self, code: str):
        try:
            await self.engine.sign_in(self.config.phone, code)
            me = await self.engine.get_me()
            return {"status": "connected",
                    "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                    "username": me.username or ""}
        except Exception as e:
            if "PASSWORD_HASH_INVALID" in str(e) or "2FA" in str(e).upper():
                return {"status": "need_2fa"}
            raise

    def _on_sign_in_result(self, result):
        self.code_btn.config(state=tk.NORMAL)
        self.pwd_btn.config(state=tk.NORMAL)
        if result is None:
            return

        status = result.get("status")
        if status == "connected":
            self._draw_dot("green")
            self._status_text.config(text="Connected", fg=THEME["green"])
            self.acct_name_label.config(text=result["name"] or "Unknown")
            self.acct_detail_label.config(text=f"@{result['username']}" if result["username"] else "")
            self.acct_card.pack(fill=tk.X, pady=(12, 0))
            self.auth_card.pack_forget()
            self.auth_msg.config(text="")
            self._set_status(f"Connected as {result['name']}")
            self._on_refresh_channels()
        elif status == "need_2fa":
            self.auth_msg.config(text="2FA is enabled. Enter your password.", fg=THEME["orange"])
            self.pwd_entry.focus_set()

    def _on_submit_password(self):
        password = self.pwd_var.get().strip()
        if not password:
            return
        self.auth_msg.config(text="Verifying 2FA...", fg=THEME["orange"])
        self.pwd_btn.config(state=tk.DISABLED)
        self._run_async(self._sign_in_2fa(password), self._on_sign_in_result)

    async def _sign_in_2fa(self, password: str):
        await self.engine.sign_in_with_password(password)
        me = await self.engine.get_me()
        return {"status": "connected",
                "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                "username": me.username or ""}

    def _on_disconnect(self):
        self._run_async(self.engine.disconnect(), self._on_disconnected)

    def _on_disconnected(self, _=None):
        self._draw_dot("gray")
        self._status_text.config(text="Disconnected", fg=THEME["fg_dim"])
        self.acct_card.pack_forget()
        self.auth_card.pack_forget()
        self._set_status("Disconnected")

    # ── Channels ───────────────────────────────────────────────────────

    def _on_refresh_channels(self):
        self.refresh_btn.config(state=tk.DISABLED)
        self._set_status("Fetching channels...")
        self._run_async(self.engine.get_channels(), self._on_channels_loaded)

    def _on_channels_loaded(self, channels):
        self.refresh_btn.config(state=tk.NORMAL)
        if channels is None:
            return

        self._channels = channels
        self._channel_vars.clear()

        # Clear existing widgets
        for w in self._channel_widgets.values():
            w.destroy()
        self._channel_widgets.clear()

        for ch in channels:
            var = tk.BooleanVar(value=False)
            self._channel_vars[ch["id"]] = var

            row = tk.Frame(self._channel_frame, bg=THEME["list_bg"])
            row.pack(fill=tk.X)

            cb = tk.Checkbutton(row, variable=var, text="",
                                bg=THEME["list_bg"], fg=THEME["fg"],
                                selectcolor=THEME["bg3"],
                                activebackground=THEME["list_bg"],
                                activeforeground=THEME["fg"],
                                command=self._update_selected_count)
            cb.pack(side=tk.LEFT, padx=(8, 4))

            kind = "📢" if ch.get("is_channel") else "👥"
            name = f"{kind}  {ch['title']}"
            if ch.get("username"):
                name += f"  (@{ch['username']})"

            lbl = tk.Label(row, text=name, bg=THEME["list_bg"], fg=THEME["fg"],
                           font=("Segoe UI", 10), anchor=tk.W)
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=3)

            self._channel_widgets[ch["id"]] = row

        self._filter_channels()

    def _filter_channels(self):
        query = self.channel_search_var.get().lower()
        visible = 0
        for ch in self._channels:
            widget = self._channel_widgets.get(ch["id"])
            if not widget:
                continue
            if query and query not in ch["title"].lower():
                widget.pack_forget()
            else:
                widget.pack(fill=tk.X)
                visible += 1
        self.channel_count_label.config(text=f"{visible} of {len(self._channels)} shown")
        self._update_selected_count()

    def _update_selected_count(self):
        count = sum(1 for v in self._channel_vars.values() if v.get())
        self.selected_count_label.config(text=f"{count} selected")

    def _on_select_all(self):
        for var in self._channel_vars.values():
            var.set(True)
        self._update_selected_count()

    def _on_deselect_all(self):
        for var in self._channel_vars.values():
            var.set(False)
        self._update_selected_count()

    # ── Downloads ──────────────────────────────────────────────────────

    def _on_browse_dir(self):
        d = filedialog.askdirectory(title="Select Download Directory")
        if d:
            self.dl_dir_var.delete(0, tk.END)
            self.dl_dir_var.insert(0, d)
            self.config.download_dir = d

    def _on_download(self):
        selected_ids = [cid for cid, var in self._channel_vars.items() if var.get()]
        if not selected_ids:
            messagebox.showwarning("No Selection", "Please select at least one channel in the Channels tab.")
            return

        dl_dir = self.dl_dir_var.get().strip()
        if not dl_dir:
            messagebox.showerror("Error", "Please specify a download directory.")
            return
        self.config.download_dir = dl_dir

        selected_channels = [ch for ch in self._channels if ch["id"] in selected_ids]
        count = len(selected_channels)
        names = "\n".join(f"  • {ch['title']}" for ch in selected_channels[:10])
        if count > 10:
            names += f"\n  ... and {count - 10} more"

        if not messagebox.askyesno("Confirm Download",
                                   f"Download all media from:\n\n{names}\n\n"
                                   f"{count} channel(s) • {self.engine.concurrent_downloads} parallel"):
            return

        self._download_active = True
        self.download_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self._notebook.select(2)  # Switch to Downloads tab

        self.engine.reset_cancel()
        self.engine.set_callbacks(log=self._append_log, speed=self._on_speed_update)
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(10)

        self._run_async(
            self.engine.download_all_channels(selected_channels, dl_dir),
            self._on_download_complete,
        )

    def _on_speed_update(self, bytes_done: int, bytes_total: int, speed_bps: float, active: int):
        def _update():
            if speed_bps > 1_000_000:
                speed_str = f"{speed_bps / 1_000_000:.1f} MB/s"
            elif speed_bps > 1_000:
                speed_str = f"{speed_bps / 1_000:.0f} KB/s"
            else:
                speed_str = f"{speed_bps:.0f} B/s"

            done_str = self._fmt_size(bytes_done)
            total_str = self._fmt_size(bytes_total)
            pct = (bytes_done / bytes_total * 100) if bytes_total > 0 else 0

            self.speed_label.config(text=f"{done_str} / {total_str}  |  {speed_str}  |  {active} active")
            if bytes_total > 0:
                self.progress_bar.configure(mode="determinate", maximum=100, value=min(pct, 100))
        self.root.after(0, _update)

    def _on_download_complete(self, stats):
        self._download_active = False
        self.download_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.progress_bar.stop()
        self.speed_label.config(text="")
        self._update_db_stats()

        if stats is None:
            self._append_log("\nDownload failed or was cancelled.")
            self._set_status("Download cancelled")
            return

        summary = (
            f"\n{'═' * 50}\n"
            f"  COMPLETE\n"
            f"  Downloaded: {stats['downloaded']}\n"
            f"  Skipped:    {stats['skipped']}\n"
            f"  Errors:     {stats['errors']}\n"
            f"  Total:      {stats['total']}\n"
            f"{'═' * 50}"
        )
        self._append_log(summary)
        self._set_status(f"Done: {stats['downloaded']} downloaded, {stats['skipped']} skipped")

        if self.config.notify_on_complete:
            self.root.after(500, lambda: messagebox.showinfo(
                "Download Complete",
                f"Downloaded: {stats['downloaded']}\n"
                f"Skipped: {stats['skipped']}\n"
                f"Errors: {stats['errors']}"
            ))

    def _on_cancel(self):
        self.engine.cancel()
        self._append_log("\n⏸ Cancelling... (may take a moment)")
        self._set_status("Cancelling...")

    def _append_log(self, msg: str):
        def _do():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ── Helpers ────────────────────────────────────────────────────────

    def _set_status(self, msg: str, timeout: int = 0):
        self._status_bar_label.config(text=msg)
        if timeout > 0:
            self.root.after(timeout, lambda: self._status_bar_label.config(text="Ready"))

    def _show_error(self, msg: str):
        messagebox.showerror("Error", msg)
        self._append_log(f"ERROR: {msg}")

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.1f} GB"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f} MB"
        if n >= 1_000:
            return f"{n / 1_000:.0f} KB"
        return f"{n} B"

    def _on_close(self):
        if self._download_active:
            if not messagebox.askyesno("Download in Progress",
                                       "A download is in progress. Cancel and exit?"):
                return
            self.engine.cancel()

        if self.config.minimize_to_tray and self._has_tray and not self._download_active:
            self._minimize_to_tray()
            return

        self._force_close()

    def run(self):
        self._running = True
        # If already configured, auto-connect
        if self.config.is_configured:
            self.root.after(500, self._auto_connect)
        self.root.mainloop()

    def _auto_connect(self):
        if self.config.is_configured:
            self.api_id_var.delete(0, tk.END)
            self.api_id_var.insert(0, str(self.config.api_id or ""))
            self.api_hash_var.delete(0, tk.END)
            self.api_hash_var.insert(0, self.config.api_hash or "")
            self.phone_var.delete(0, tk.END)
            self.phone_var.insert(0, self.config.phone or "")
            self._on_connect()
