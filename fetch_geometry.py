"""
fetch_geometry.py  —  Fetch TM arena geometry (SVG background + seat JSON) for a team

Opens a TM event page, intercepts from mapsapi.tmol.io:
  - Arena background SVG  → data/{team}/arena_full.svg
  - Seat geometry JSON    → data/{team}/seatmap_geo.json
  - DOM-extracted SVG     → data/{team}/arena.svg  (section hit areas with data-section-name)

Usage:
    python fetch_geometry.py <ticketmaster_event_url> <team_slug>

    e.g.  python fetch_geometry.py "https://www.ticketmaster.com/event/..." celtics

After running, use:
    python generate_team_story.py <team_slug>
"""

import json, os, sys, time
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

CHROME_PROFILE_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tm_chrome_profile")


def fetch(url: str, team_slug: str):
    print(f"Launching browser for {team_slug}...")
    pw = sync_playwright().start()
    profile = os.path.join(CHROME_PROFILE_BASE, team_slug + "_geo")
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

    captured = {
        "svg_body": None,
        "svg_url":  None,
        "geo_body": None,
        "geo_url":  None,
    }

    def on_response(resp):
        url_r = resp.url
        if "mapsapi.tmol.io" not in url_r:
            return

        ct = resp.headers.get("content-type", "")
        print(f"  [net] {resp.status} {ct[:35]:35s} {url_r[:120]}")

        # Capture the SVG URL so we can fetch the body directly later
        # Skip sectionLevel-only version — it strips the court
        if "staticImage" in url_r and "sectionLevel=true" in url_r:
            return
        if captured["svg_url"] is None and (
            "svg" in ct or url_r.endswith(".svg") or "/image/" in url_r or "staticImage" in url_r
        ):
            captured["svg_url"] = url_r
            # Try body immediately
            try:
                body = resp.body()
                if body and len(body) > 1000:
                    captured["svg_body"] = body
                    print(f"  ✓ Arena SVG captured ({len(body):,} bytes)")
            except Exception:
                pass  # Will re-fetch via page.request below

        # Seat geometry JSON (placeDetailNoKeys)
        if captured["geo_url"] is None and "placeDetailNoKeys" in url_r:
            captured["geo_url"] = url_r
            try:
                body = resp.body()
                if body:
                    json.loads(body)
                    captured["geo_body"] = body
                    print(f"  ✓ Seat geometry JSON captured ({len(body):,} bytes)")
            except Exception:
                pass

    page.on("response", on_response)

    try:
        print(f"Loading: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        print("Waiting for geometry to load (up to 60s)...")
        for i in range(60):
            time.sleep(1)
            done_svg = captured["svg_body"] is not None
            done_geo = captured["geo_body"] is not None
            if done_svg and done_geo:
                break
            status = ("SVG✓" if done_svg else "SVG…") + " " + ("GEO✓" if done_geo else "GEO…")
            print(f"  {i+1}s — {status}")

        # If SVG still not found, try scrolling/clicking to trigger map load
        if not captured["svg_body"] or not captured["geo_body"]:
            print("  Trying to trigger map load (click/scroll)...")
            try:
                page.mouse.move(700, 400)
                page.mouse.scroll(0, 200)
                time.sleep(3)
                page.mouse.move(700, 400)
                page.mouse.click(700, 400)
            except Exception:
                pass
            for i in range(20):
                time.sleep(1)
                if captured["svg_body"] and captured["geo_body"]:
                    break
                status = ("SVG✓" if captured["svg_body"] else "SVG…") + " " + ("GEO✓" if captured["geo_body"] else "GEO…")
                print(f"  +{i+1}s — {status}")

        # If SVG URL was seen but body not captured, fetch it directly
        if captured["svg_url"] and not captured["svg_body"]:
            # Strip sectionLevel parameter to get full arena with court
            svg_fetch_url = re.sub(r'[&?]sectionLevel=[^&]*', '', captured["svg_url"])
            print(f"\n  Re-fetching SVG via page.request...")
            try:
                r = page.request.get(svg_fetch_url)
                body = r.body()
                if body and len(body) > 1000:
                    captured["svg_body"] = body
                    print(f"  ✓ SVG fetched ({len(body):,} bytes)")
            except Exception as e:
                print(f"  SVG re-fetch failed: {e}")

        # If no SVG URL seen at all, try constructing the full staticImage URL from event ID
        if not captured["svg_body"]:
            import re as _re2
            m2 = _re2.search(r'/event/([A-Z0-9]+)', url)
            if m2:
                eid = m2.group(1)
                svg_url = f"https://mapsapi.tmol.io/maps/geometry/3/event/{eid}/staticImage?systemId=HOST&app=PRD2663_EDP_NA&avertaFonts=true"
                print(f"\n  Trying direct SVG fetch...")
                try:
                    r = page.request.get(svg_url)
                    body = r.body()
                    if body and len(body) > 10000:
                        captured["svg_body"] = body
                        print(f"  ✓ SVG fetched directly ({len(body):,} bytes)")
                except Exception as e:
                    print(f"  Direct SVG fetch failed: {e}")

        # If geometry URL was seen but body not captured, fetch directly
        if captured["geo_url"] and not captured["geo_body"]:
            print(f"  Re-fetching geo JSON: {captured['geo_url'][:80]}...")
            try:
                r = page.request.get(captured["geo_url"])
                body = r.body()
                json.loads(body)
                captured["geo_body"] = body
                print(f"  ✓ Geo JSON fetched ({len(body):,} bytes)")
            except Exception as e:
                print(f"  Geo re-fetch failed: {e}")

        # If geo URL never appeared, try fetching it directly using event ID from URL
        if not captured["geo_body"]:
            import re as _re
            m = _re.search(r'/event/([A-Z0-9]+)', url)
            if m:
                eid = m.group(1)
                geo_url = f"https://mapsapi.tmol.io/maps/geometry/3/event/{eid}/placeDetailNoKeys?useHostGrids=true&systemId=HOST"
                print(f"\n  Trying direct geometry fetch: {geo_url[:80]}...")
                try:
                    r = page.request.get(geo_url)
                    body = r.body()
                    json.loads(body)
                    captured["geo_body"] = body
                    captured["geo_url"]  = geo_url
                    print(f"  ✓ Geo JSON fetched directly ({len(body):,} bytes)")
                except Exception as e:
                    print(f"  Direct geo fetch failed: {e}")

        # Save arena_full.svg
        if captured["svg_body"]:
            path = f"{out_dir}/arena_full.svg"
            with open(path, "wb") as f:
                f.write(captured["svg_body"])
            print(f"\n✓ arena_full.svg → {path}  ({len(captured['svg_body']):,} bytes)")
        else:
            print("\n✗ No arena SVG captured")

        # Save seatmap_geo.json
        if captured["geo_body"]:
            path = f"{out_dir}/seatmap_geo.json"
            with open(path, "wb") as f:
                f.write(captured["geo_body"])
            print(f"✓ seatmap_geo.json → {path}  ({len(captured['geo_body']):,} bytes)")
        else:
            print("✗ No seat geometry JSON captured")

        # Extract DOM SVG (has data-section-name attributes for section hit areas)
        print("\nExtracting DOM SVG (section outlines)...")
        svg_content = page.evaluate("""() => {
            const svgs = [...document.querySelectorAll('svg')];
            const arena = svgs.find(s => {
                const bb = s.getBoundingClientRect();
                return bb.width > 300 && bb.height > 200;
            });
            return arena ? arena.outerHTML : null;
        }""")

        if svg_content:
            path = f"{out_dir}/arena.svg"
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg_content)
            print(f"✓ arena.svg (DOM) → {path}  ({len(svg_content):,} chars)")
        else:
            print("✗ No DOM SVG found")

        all_ok = captured["svg_body"] and captured["geo_body"] and svg_content
        if all_ok:
            print(f"\n✅ All geometry files saved for {team_slug}")
            print(f"   Run: python generate_team_story.py {team_slug}")
        else:
            print(f"\n⚠️  Some files missing — check output above")

        print("\nKeeping browser open 15s for inspection...")
        time.sleep(15)

    finally:
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python fetch_geometry.py <ticketmaster_event_url> <team_slug>")
        sys.exit(1)
    fetch(sys.argv[1], sys.argv[2])
