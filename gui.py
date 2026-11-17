"""
Tkinter GUI for Telegram Media Downloader.
"""
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

from config import Config
from core import TelegramEngine


class TelegramDownloaderGUI:
    """Main application GUI."""

    def __init__(self):
        self.config = Config()
        self.engine = TelegramEngine(self.config)
        self.loop = asyncio.new_event_loop()
        self._running = False

        self.root = tk.Tk()
        self.root.title("Telegram Media Downloader")
        self.root.geometry("720x600")
        self.root.minsize(600, 500)

        # Style
        self.style = ttk.Style()
        self.style.theme_use("clam")

        self._build_ui()
        self._center_window()

        # Start async loop in background thread
        self._start_async_thread()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _start_async_thread(self):
        """Run the asyncio event loop in a background thread."""

        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        t = threading.Thread(target=run_loop, daemon=True)
        t.start()

    def _run_async(self, coro, callback=None):
        """Schedule a coroutine on the background loop."""
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

    def _build_ui(self):
        # Main container
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # ---- API Credentials Frame ----
        creds_frame = ttk.LabelFrame(main, text="API Credentials", padding=10)
        creds_frame.pack(fill=tk.X, pady=(0, 10))

        # api_id
        ttk.Label(creds_frame, text="API ID:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.api_id_var = tk.StringVar(value=str(self.config.api_id or ""))
        self.api_id_entry = ttk.Entry(creds_frame, textvariable=self.api_id_var, width=20)
        self.api_id_entry.grid(row=0, column=1, sticky=tk.W, padx=(5, 20), pady=2)

        # api_hash
        ttk.Label(creds_frame, text="API Hash:").grid(row=0, column=2, sticky=tk.W, pady=2)
        self.api_hash_var = tk.StringVar(value=self.config.api_hash or "")
        self.api_hash_entry = ttk.Entry(creds_frame, textvariable=self.api_hash_var, width=40, show="*")
        self.api_hash_entry.grid(row=0, column=3, sticky=tk.EW, padx=(5, 0), pady=2)

        # Phone
        ttk.Label(creds_frame, text="Phone:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.phone_var = tk.StringVar(value=self.config.phone or "")
        self.phone_entry = ttk.Entry(creds_frame, textvariable=self.phone_var, width=20)
        self.phone_entry.grid(row=1, column=1, sticky=tk.W, padx=(5, 20), pady=2)

        # Save creds + Connect button
        btn_frame = ttk.Frame(creds_frame)
        btn_frame.grid(row=1, column=2, columnspan=2, sticky=tk.E, pady=2)
        self.save_creds_btn = ttk.Button(btn_frame, text="Save & Connect", command=self._on_connect)
        self.save_creds_btn.pack(side=tk.RIGHT, padx=(5, 0))

        self.status_label = ttk.Label(creds_frame, text="Not connected", foreground="gray")
        self.status_label.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(5, 0))

        creds_frame.columnconfigure(3, weight=1)

        # ---- Auth Frame (dynamic) ----
        self.auth_frame = ttk.LabelFrame(main, text="Authentication", padding=10)

        ttk.Label(self.auth_frame, text="Verification Code:").grid(row=0, column=0, sticky=tk.W)
        self.code_var = tk.StringVar()
        self.code_entry = ttk.Entry(self.auth_frame, textvariable=self.code_var, width=15)
        self.code_entry.grid(row=0, column=1, sticky=tk.W, padx=5)

        self.submit_code_btn = ttk.Button(self.auth_frame, text="Submit Code", command=self._on_submit_code)
        self.submit_code_btn.grid(row=0, column=2, padx=5)

        ttk.Label(self.auth_frame, text="2FA Password:").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        self.pwd_var = tk.StringVar()
        self.pwd_entry = ttk.Entry(self.auth_frame, textvariable=self.pwd_var, width=15, show="*")
        self.pwd_entry.grid(row=1, column=1, sticky=tk.W, padx=5, pady=(5, 0))

        self.submit_pwd_btn = ttk.Button(self.auth_frame, text="Submit Password", command=self._on_submit_password)
        self.submit_pwd_btn.grid(row=1, column=2, padx=5, pady=(5, 0))

        self.auth_label = ttk.Label(self.auth_frame, text="", foreground="blue")
        self.auth_label.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))

        # ---- Channels Frame ----
        channels_frame = ttk.LabelFrame(main, text="Channels & Groups", padding=10)
        channels_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Channel list with scrollbar
        list_frame = ttk.Frame(channels_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.channel_listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.channel_listbox.yview)
        self.channel_listbox.configure(yscrollcommand=scrollbar.set)

        self.channel_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Channel buttons
        chan_btn_frame = ttk.Frame(channels_frame)
        chan_btn_frame.pack(fill=tk.X, pady=(5, 0))
        self.refresh_btn = ttk.Button(chan_btn_frame, text="Refresh Channels", command=self._on_refresh_channels)
        self.refresh_btn.pack(side=tk.LEFT)
        self.select_all_btn = ttk.Button(chan_btn_frame, text="Select All", command=self._on_select_all)
        self.select_all_btn.pack(side=tk.LEFT, padx=(5, 0))
        self.deselect_btn = ttk.Button(chan_btn_frame, text="Deselect All", command=self._on_deselect_all)
        self.deselect_btn.pack(side=tk.LEFT, padx=(5, 0))

        # ---- Download Frame ----
        dl_frame = ttk.LabelFrame(main, text="Download", padding=10)
        dl_frame.pack(fill=tk.X)

        # Download directory
        dir_frame = ttk.Frame(dl_frame)
        dir_frame.pack(fill=tk.X)
        ttk.Label(dir_frame, text="Save to:").pack(side=tk.LEFT)
        self.dl_dir_var = tk.StringVar(value=self.config.download_dir)
        self.dl_dir_entry = ttk.Entry(dir_frame, textvariable=self.dl_dir_var)
        self.dl_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.browse_btn = ttk.Button(dir_frame, text="Browse...", command=self._on_browse_dir)
        self.browse_btn.pack(side=tk.RIGHT)

        # Download button + progress
        ctrl_frame = ttk.Frame(dl_frame)
        ctrl_frame.pack(fill=tk.X, pady=(10, 0))

        self.download_btn = ttk.Button(ctrl_frame, text="Start Download", command=self._on_download)
        self.download_btn.pack(side=tk.LEFT)

        self.cancel_btn = ttk.Button(ctrl_frame, text="Cancel", command=self._on_cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(5, 0))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(ctrl_frame, variable=self.progress_var, mode="indeterminate")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))

        # Log area
        self.log_text = tk.Text(dl_frame, height=6, state=tk.DISABLED, bg="#1e1e1e", fg="#d4d4d4",
                                font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self._channels = []  # Store channel data

    # ---- Event Handlers ----

    def _on_connect(self):
        """Save credentials and connect to Telegram."""
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

        self.status_label.config(text="Connecting...", foreground="orange")
        self.save_creds_btn.config(state=tk.DISABLED)
        self._run_async(self._connect_and_auth(), self._on_connect_result)

    async def _connect_and_auth(self):
        """Async connect + auth flow."""
        connected = await self.engine.connect()
        if connected:
            me = await self.engine.get_me()
            return {"status": "connected", "user": f"{me.first_name or ''} (@{me.username or 'no username'})"}
        else:
            # Need to send code
            phone = self.config.phone
            if not phone:
                raise ValueError("Phone number is required for first-time login.")
            await self.engine.send_code(phone)
            return {"status": "code_sent", "phone": phone}

    def _on_connect_result(self, result):
        self.save_creds_btn.config(state=tk.NORMAL)
        if result is None:
            return

        status = result.get("status")
        if status == "connected":
            self.status_label.config(
                text=f"Connected as {result['user']}", foreground="green"
            )
            self.auth_frame.pack_forget()
            self._on_refresh_channels()
        elif status == "code_sent":
            self.status_label.config(
                text=f"Code sent to {result['phone']}. Enter code below.",
                foreground="blue",
            )
            self.auth_frame.pack(fill=tk.X, pady=(0, 10), after=self.status_label.master)

    def _on_submit_code(self):
        code = self.code_var.get().strip()
        if not code:
            return

        self.auth_label.config(text="Verifying...", foreground="orange")
        self.submit_code_btn.config(state=tk.DISABLED)
        self._run_async(self._sign_in(code), self._on_sign_in_result)

    async def _sign_in(self, code: str):
        try:
            await self.engine.sign_in(self.config.phone, code)
            me = await self.engine.get_me()
            return {"status": "connected", "user": f"{me.first_name or ''} (@{me.username or 'no username'})"}
        except Exception as e:
            if "PASSWORD_HASH_INVALID" in str(e) or "2FA" in str(e).upper():
                return {"status": "need_2fa"}
            raise

    def _on_sign_in_result(self, result):
        self.submit_code_btn.config(state=tk.NORMAL)
        if result is None:
            return

        status = result.get("status")
        if status == "connected":
            self.status_label.config(
                text=f"Connected as {result['user']}", foreground="green"
            )
            self.auth_label.config(text="")
            self.auth_frame.pack_forget()
            self._on_refresh_channels()
        elif status == "need_2fa":
            self.auth_label.config(
                text="2FA is enabled. Enter your password below.",
                foreground="blue",
            )

    def _on_submit_password(self):
        password = self.pwd_var.get().strip()
        if not password:
            return

        self.auth_label.config(text="Verifying 2FA...", foreground="orange")
        self.submit_pwd_btn.config(state=tk.DISABLED)
        self._run_async(self._sign_in_2fa(password), self._on_sign_in_result)

    async def _sign_in_2fa(self, password: str):
        await self.engine.sign_in_with_password(password)
        me = await self.engine.get_me()
        return {"status": "connected", "user": f"{me.first_name or ''} (@{me.username or 'no username'})"}

    def _on_refresh_channels(self):
        self.refresh_btn.config(state=tk.DISABLED)
        self._append_log("Fetching channels...")
        self._run_async(self.engine.get_channels(), self._on_channels_loaded)

    def _on_channels_loaded(self, channels):
        self.refresh_btn.config(state=tk.NORMAL)
        if channels is None:
            return

        self._channels = channels
        self.channel_listbox.delete(0, tk.END)

        for ch in channels:
            kind = "📢" if ch["is_channel"] else "👥"
            label = f"{kind} {ch['title']}"
            if ch["username"]:
                label += f" (@{ch['username']})"
            self.channel_listbox.insert(tk.END, label)

        self._append_log(f"Found {len(channels)} channels/groups.")

    def _on_select_all(self):
        self.channel_listbox.select_set(0, tk.END)

    def _on_deselect_all(self):
        self.channel_listbox.selection_clear(0, tk.END)

    def _on_browse_dir(self):
        d = filedialog.askdirectory(title="Select Download Directory")
        if d:
            self.dl_dir_var.set(d)
            self.config.download_dir = d

    def _on_download(self):
        selected = self.channel_listbox.curselection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one channel.")
            return

        dl_dir = self.dl_dir_var.get().strip()
        if not dl_dir:
            messagebox.showerror("Error", "Please specify a download directory.")
            return
        self.config.download_dir = dl_dir

        selected_channels = [self._channels[i] for i in selected]
        count = len(selected_channels)
        names = ", ".join(ch["title"] for ch in selected_channels)

        if not messagebox.askyesno(
            "Confirm Download",
            f"Download all photos and videos from:\n\n{names}\n\n({count} channel(s))",
        ):
            return

        self.download_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress_bar.start(10)
        self.engine.reset_cancel()
        self.engine.set_callbacks(log=self._append_log)

        self._run_async(
            self._download_all(selected_channels, dl_dir),
            self._on_download_complete,
        )

    async def _download_all(self, channels: list[dict], dl_dir: str):
        total_stats = {"downloaded": 0, "skipped": 0, "errors": 0, "total": 0}
        for ch in channels:
            self.engine._log(f"\n--- Processing: {ch['title']} ---")
            stats = await self.engine.download_channel_media(
                ch["id"], ch["title"], dl_dir
            )
            for k in total_stats:
                total_stats[k] += stats.get(k, 0)
        return total_stats

    def _on_download_complete(self, stats):
        self.download_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.progress_bar.stop()
        self.progress_var.set(0)

        if stats is None:
            self._append_log("Download failed or was cancelled.")
            return

        self._append_log(
            f"\n=== COMPLETE ===\n"
            f"Downloaded: {stats['downloaded']}\n"
            f"Skipped (already had): {stats['skipped']}\n"
            f"Errors: {stats['errors']}\n"
            f"Total media found: {stats['total']}"
        )

    def _on_cancel(self):
        self.engine.cancel()
        self._append_log("Cancelling...")

    def _append_log(self, msg: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _show_error(self, msg: str):
        messagebox.showerror("Error", msg)
        self._append_log(f"ERROR: {msg}")

    def _on_close(self):
        self._running = False
        if self.engine:
            future = asyncio.run_coroutine_threadsafe(
                self.engine.disconnect(), self.loop
            )
            future.result(timeout=3)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()

    def run(self):
        self._running = True
        self.root.mainloop()
