"""
explore_seats.py

One-off tool to discover TM's seat-level API endpoint.

Loads a TM event page, waits for the facets XHR (same as main scraper),
then tries clicking on each section in the seat map and logs every new
XHR that fires — so we can identify the seat-level endpoint and its
response structure.

Usage:
    python explore_seats.py <event_url>

    e.g. python explore_seats.py https://www.ticketmaster.com/event/07006306B45125A3
"""

import sys
import json
import time
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

WAIT_MS      = 12000
CHROME_PROFILE = ".tm_chrome_profile/explorer"


def explore(event_url: str):
    pw  = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        CHROME_PROFILE,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    page = ctx.new_page()
    Stealth().apply_stealth_sync(page)

    # ── Capture all XHRs ──────────────────────────────────────────────────────
    seen_urls = set()
    interesting = []

    IGNORE = ("google", "analytics", "fonts", "facebook", "twitter",
              "doubleclick", "cdn", ".css", ".js", ".png", ".svg", ".woff")

    def on_response(resp):
        url = resp.url
        if any(x in url for x in IGNORE):
            return
        if url in seen_urls:
            return
        seen_urls.add(url)
        ct = resp.headers.get("content-type", "")
        if "json" in ct or "ticketmaster" in url:
            print(f"  [XHR] {url[:120]}")
            interesting.append({"url": url, "status": resp.status, "ct": ct})

    page.on("response", on_response)

    # ── Phase 1: load the page (same as main scraper) ─────────────────────────
    print(f"\n[1] Loading event page...")
    page.goto(event_url, wait_until="load", timeout=60000)
    page.wait_for_timeout(WAIT_MS)

    # ── Phase 2: find section elements and click them ─────────────────────────
    print(f"\n[2] Looking for seat map sections to click...")

    # TM renders sections as SVG paths or divs with data-section-id attributes
    # Try multiple selectors TM has used historically
    section_selectors = [
        "[data-section-id]",
        "[data-id]",
        ".seatmap__section",
        "g[id^='s_']",        # SVG group per section
        "path[data-section]",
        "[class*='section']",
    ]

    clicked = 0
    for sel in section_selectors:
        els = page.query_selector_all(sel)
        if els:
            print(f"  Found {len(els)} elements with selector '{sel}'")
            # Click first 3 sections to see what fires
            for i, el in enumerate(els[:3]):
                try:
                    print(f"  Clicking section {i+1}...")
                    el.scroll_into_view_if_needed()
                    el.click(timeout=5000)
                    page.wait_for_timeout(3000)
                    clicked += 1
                except Exception as ex:
                    print(f"    Skip: {ex}")
            break

    if not clicked:
        print("  No section elements found — trying to scroll/interact with page...")
        page.mouse.move(640, 400)
        page.wait_for_timeout(2000)
        page.mouse.click(640, 400)
        page.wait_for_timeout(3000)

    # ── Phase 3: report findings ───────────────────────────────────────────────
    print(f"\n[3] All unique JSON/TM XHRs captured ({len(interesting)} total):")
    print("="*80)

    seat_candidates = []
    for r in interesting:
        url = r["url"]
        print(f"\n  URL: {url}")
        # Flag anything that looks seat-related
        keywords = ("seat", "row", "avail", "pick", "select", "chart", "map", "inventory")
        if any(k in url.lower() for k in keywords):
            print("  *** POSSIBLE SEAT-LEVEL ENDPOINT ***")
            seat_candidates.append(url)

    if seat_candidates:
        print(f"\n\n{'='*80}")
        print(f"SEAT CANDIDATES ({len(seat_candidates)}):")
        for u in seat_candidates:
            print(f"  {u}")

        # Try fetching the first candidate and print structure
        print(f"\n[4] Fetching first candidate to inspect structure...")
        try:
            resp = page.request.get(seat_candidates[0])
            data = resp.json()
            print(json.dumps(data, indent=2)[:3000])
        except Exception as ex:
            print(f"  Could not fetch: {ex}")
    else:
        print("\n  No obvious seat-level endpoints found in this run.")
        print("  Check the full XHR list above manually.")

    print("\n[done] Keeping browser open for 30s for manual inspection...")
    time.sleep(30)
    ctx.close()
    pw.stop()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python explore_seats.py <event_url>")
        sys.exit(1)
    explore(sys.argv[1])
