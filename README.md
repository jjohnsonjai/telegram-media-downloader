# Telegram Media Downloader

Bulk download photos and videos from Telegram channels and groups — including **private channels** you're a member of.

## Features

- **Full Telegram login** — authenticates as your account via Telethon MTProto
- **Private channel support** — accesses any channel/group you've joined, even without a public username
- **Bulk download** — grab all photos and videos from selected channels at once
- **Skip already downloaded** — SQLite-backed dedup so you never re-download the same file
- **Organized output** — files saved to `channel-name/` folders with timestamps
- **Simple GUI** — Tkinter interface: connect, pick channels, download
- **Desktop shortcut** — one-click launch with custom icon

## Setup

### 1. Get API Credentials

Go to [my.telegram.org](https://my.telegram.org/apps), create an app, and get your:
- **API ID** (a number)
- **API Hash** (a hex string)

### 2. Install

```bash
pip install -r requirements.txt
```

### 3. Run

```bash
python main.py
```

Or use the desktop shortcut: **Telegram Media Downloader**

### 4. Login

1. Enter your API ID, API Hash, and phone number
2. Click **Save & Connect**
3. Enter the verification code Telegram sends you
4. If you have 2FA enabled, enter your password

Your session is saved — you won't need to re-authenticate next time.

## How It Works

- Authenticates to Telegram's MTProto API as your user account
- Lists all channels and groups you're a member of (including private ones)
- Iterates through message history downloading every photo and video
- Tracks downloaded file IDs in a local SQLite database
- Skips files you already have on subsequent runs

## File Structure

```
telegram-media-downloader/
├── main.py           # Entry point
├── core.py           # Telethon engine (auth, channel listing, downloading)
├── gui.py            # Tkinter GUI
├── config.py         # Configuration persistence
├── generate_icon.py  # Icon generator
├── icon.ico          # App icon
├── requirements.txt  # Python dependencies
└── README.md
```

## Data Storage

| What | Where |
|------|-------|
| Session (auth) | `%APPDATA%\TelegramMediaDownloader\session.session` |
| Config (API keys) | `%APPDATA%\TelegramMediaDownloader\config.json` |
| Download tracker | `%APPDATA%\TelegramMediaDownloader\downloads.db` |
| Downloaded media | `%USERPROFILE%\Downloads\TelegramMedia\` (configurable) |
