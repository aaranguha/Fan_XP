"""
set_telegram_webhook.py

One-time setup: tells Telegram to POST new messages for the Fan XP bot to
the deployed API's /webhooks/telegram endpoint, with a secret token so the
endpoint can verify requests actually came from Telegram.

Usage:
    python set_telegram_webhook.py

Requires in .env:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_WEBHOOK_SECRET   (generate one if you don't have it -- any
                               random string works, must match what's set
                               as TELEGRAM_WEBHOOK_SECRET on the deployed API)
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://fanxp-api.onrender.com"


def main():
    token  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)
    if not secret:
        print("TELEGRAM_WEBHOOK_SECRET not set in .env")
        sys.exit(1)

    webhook_url = f"{API_BASE}/webhooks/telegram"
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        data={"url": webhook_url, "secret_token": secret},
        timeout=15,
    )
    print(resp.status_code, resp.json())

    info = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=15)
    print(info.json())


if __name__ == "__main__":
    main()
