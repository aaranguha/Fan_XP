"""
Run this ONCE on the old Intel Mac to log into TM and save the session.
The saved profile is reused by all scrapers so TM doesn't flag bot traffic.

Usage:
    python tm_login.py
"""
import os
from playwright.sync_api import sync_playwright

profile = os.path.join(os.getcwd(), ".tm_chrome_profile", "shared")
os.makedirs(profile, exist_ok=True)
print(f"Using profile: {profile}")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        profile,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    page = ctx.new_page()
    page.goto("https://www.ticketmaster.com/login")
    print("\nA browser window opened. Log in to your Ticketmaster account.")
    print("When done, press Enter here to save the session and close.")
    input()
    ctx.close()

print("Session saved. The scraper will now use this logged-in profile.")
