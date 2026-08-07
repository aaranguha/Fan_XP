"""
Quick diagnostic: open a TM event page locally and log ALL XHR requests.
Run this to see what page TM shows and what URLs are captured.

    python scrape_halftime_now.py <TM_event_URL>
    e.g. python scrape_halftime_now.py https://www.ticketmaster.com/event/Z7r9jZ1A7QafM
"""
import sys
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

url = sys.argv[1] if len(sys.argv) > 1 else "https://www.ticketmaster.com"
print(f"Loading: {url}")

all_requests = []

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        "/tmp/test_tm_profile",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    page = ctx.new_page()
    Stealth().apply_stealth_sync(page)

    def on_req(req):
        u = req.url
        if any(k in u for k in ["ticketmaster.com", "facets", "inventory", "seating"]):
            all_requests.append(u)

    page.on("request", on_req)
    page.goto(url, wait_until="load", timeout=45000)
    page.wait_for_timeout(15000)

    print(f"\nPage title: {page.title()}")
    print(f"Final URL:  {page.url}")
    print(f"\nCaptured TM-related requests ({len(all_requests)}):")
    for r in all_requests[:20]:
        print(f"  {r[:120]}")

    page.screenshot(path="/tmp/tm_test.png")
    print("\nScreenshot: /tmp/tm_test.png")

    ctx.close()
    pw.stop()
