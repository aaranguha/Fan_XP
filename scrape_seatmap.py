"""
scrape_seatmap.py

Opens a TM event page, intercepts the arena background SVG from mapsapi.tmol.io,
and saves it to data/<team>/kia_center.svg for use as a visualization background.

Usage:
    python scrape_seatmap.py <url> <team_slug>
    e.g. python scrape_seatmap.py "https://www.ticketmaster.com/event/..." magic
"""

import json, os, sys, time
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

CHROME_PROFILE_BASE = os.path.expanduser("~/.tm_chrome_profile")


def scrape(url: str, team_slug: str):
    print(f"Launching browser for {team_slug}...")
    pw = sync_playwright().start()
    profile = os.path.join(CHROME_PROFILE_BASE, team_slug + "_seatmap")
    ctx = pw.chromium.launch_persistent_context(
        profile, headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1400, "height": 900},
        locale="en-US",
    )
    page = ctx.new_page()
    Stealth().apply_stealth_sync(page)

    out_dir = f"data/{team_slug}"
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, "arena.svg")

    captured = {"svg": None, "svg_url": None}

    # ── Intercept all responses from mapsapi ───────────────────────────────────
    def on_response(resp):
        url_r = resp.url
        if "mapsapi.tmol.io" not in url_r:
            return

        ct = resp.headers.get("content-type", "")
        print(f"  [net] {resp.status} {ct[:40]:40s} {url_r[:100]}")

        # Grab the SVG background image
        if ("svg" in ct or url_r.endswith(".svg") or "/image/" in url_r) \
                and captured["svg"] is None:
            try:
                body = resp.body()
                if body and (body[:5] == b"<?xml" or body[:4] == b"<svg" or b"<svg" in body[:200]):
                    captured["svg"] = body
                    captured["svg_url"] = url_r
                    print(f"  ✓ SVG captured ({len(body):,} bytes) from {url_r}")
            except Exception as e:
                print(f"  SVG body error: {e}")

    page.on("response", on_response)

    try:
        print(f"Loading: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for map to render — up to 20 seconds, checking every second
        print("Waiting for arena SVG to load...")
        for i in range(20):
            time.sleep(1)
            if captured["svg"]:
                break
            print(f"  {i+1}s — still waiting...")

        if captured["svg"]:
            with open(svg_path, "wb") as f:
                f.write(captured["svg"])
            print(f"\n✓ Arena SVG saved → {svg_path}  ({len(captured['svg']):,} bytes)")
            print(f"  Source URL: {captured['svg_url']}")
        else:
            print("\n✗ No SVG captured. Trying to extract inline SVG from DOM...")

            # Fallback: try to grab the SVG directly from the DOM
            svg_content = page.evaluate("""() => {
                // Look for large SVGs (the arena map)
                const svgs = [...document.querySelectorAll('svg')];
                const arena = svgs.find(s => {
                    const bb = s.getBoundingClientRect();
                    return bb.width > 300 && bb.height > 200;
                });
                return arena ? arena.outerHTML : null;
            }""")

            if svg_content:
                with open(svg_path, "w", encoding="utf-8") as f:
                    f.write(svg_content)
                print(f"✓ DOM SVG extracted → {svg_path}  ({len(svg_content):,} chars)")
            else:
                print("✗ No SVG found in DOM either.")
                # List all mapsapi URLs we saw for debugging
                print("\nAll mapsapi responses were logged above.")

        print("\nKeeping browser open 30s for manual inspection...")
        time.sleep(30)

    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scrape_seatmap.py <url> <team_slug>")
        sys.exit(1)
    scrape(sys.argv[1], sys.argv[2])
