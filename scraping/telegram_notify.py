"""
telegram_notify.py

Minimal Telegram sender for scrape status alerts. Reads
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment; if either
is missing, send_telegram() silently no-ops so this never breaks a
scrape run over a notification not being configured.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()


def send_telegram(message: str) -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=10,
        )
    except Exception as e:
        print(f"  [telegram] send failed: {e}")
