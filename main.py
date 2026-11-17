#!/usr/bin/env python3
"""
Telegram Media Downloader
Bulk download photos and videos from Telegram channels and groups.

Usage:
    python main.py          # Launch GUI
    python main.py --cli    # CLI mode (future)
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import TelegramDownloaderGUI


def main():
    app = TelegramDownloaderGUI()
    app.run()


if __name__ == "__main__":
    main()
