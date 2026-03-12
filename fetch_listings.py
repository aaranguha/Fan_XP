"""
fetch_listings.py

1. Use Ticketmaster Discovery API to find the next home game for a given team.
2. Open the TM event page in headless Chrome (Playwright) to establish a session.
3. Intercept two XHR calls:
     - services.ticketmaster.com/facets?by=section+seating... → ALL available seats (primary + resale)
     - offeradapter.ticketmaster.com/facets?by=offers&show=totalpricerange → offer → price map
4. Join offer IDs with prices.
5. Append results to data/<team>/listings.csv with a scraped_at timestamp and snapshot label.

Usage:
    python fetch_listings.py <team_slug> pre_game    ← run ~1hr before tip-off
    python fetch_listings.py <team_slug> halftime    ← run ~2min before halftime

    e.g.  python fetch_listings.py warriors pre_game
          python fetch_listings.py lakers halftime

Then run compare_snapshots.py to identify confirmed no-show seats.

Setup:
    pip install -r requirements.txt
    playwright install chromium
    Add TICKETMASTER_API_KEY to .env
"""

import os
import csv
import sys
import requests
from collections import Counter
from datetime import datetime, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from teams import get_team, game_dir, pre_game_csv, halftime_csv, data_dir

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TM_API_KEY = os.getenv("TICKETMASTER_API_KEY", "")
WAIT_MS    = 12000   # ms to wait after page load for all XHR calls to fire


# ── Event discovery ───────────────────────────────────────────────────────────

def find_next_home_game(tm_keyword: str, game_date: str | None = None) -> dict:
    """
    Return the home game for the given team via TM Discovery API.
    If game_date (YYYY-MM-DD) is provided, restrict results to that day only.
    """
    if not TM_API_KEY:
        raise RuntimeError("TICKETMASTER_API_KEY not set in .env")

    resp = requests.get(
        "https://app.ticketmaster.com/discovery/v2/events.json",
        params={
            "apikey":             TM_API_KEY,
            "keyword":            tm_keyword,
            "classificationName": "Basketball",
            "sort":               "date,asc",
            "size":               10,
        },
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json().get("_embedded", {}).get("events", [])
    if not events:
        raise RuntimeError(f"No home game found for '{tm_keyword}' on {game_date or 'upcoming dates'}.")

    # Filter by localDate if provided — avoids UTC midnight rollover issues
    if game_date:
        events = [
            e for e in events
            if e.get("dates", {}).get("start", {}).get("localDate") == game_date
        ]
        if not events:
            raise RuntimeError(f"No home game found for '{tm_keyword}' on {game_date}.")

    # Filter out G-League affiliates
    keyword_lower = tm_keyword.lower()
    for event in events:
        if event.get("name", "").lower().startswith(keyword_lower):
            return event
    return events[0]


# ── Browser scraping ──────────────────────────────────────────────────────────

def scrape_listings(event_url: str) -> tuple[list[dict], dict]:
    """
    Load the TM event page, intercept two XHR calls:
      - services.ticketmaster.com full-inventory facets → ALL available seats (primary + resale)
      - offeradapter price facets → offer_id → price map

    Returns:
        (facets, offer_price_map)
        facets          — list of raw facet dicts, one per listing group
        offer_price_map — {offer_id: price_usd}
    """
    stealth_config  = Stealth()
    offer_price_map: dict[str, float] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = ctx.new_page()
        stealth_config.apply_stealth_sync(page)

        # Capture two endpoints:
        #   inventory — services.ticketmaster.com facets with section+seating (all seats)
        #   pricing   — offeradapter facets with by=offers (offer→price map)
        captured = {"inventory": None, "pricing": None}

        def on_request(req):
            url = req.url
            if "services.ticketmaster.com" in url and "facets" in url \
                    and "section" in url and "seating" in url \
                    and not captured["inventory"]:
                captured["inventory"] = {"url": url, "headers": dict(req.headers)}
            elif "offeradapter" in url and "facets" in url \
                    and "by=offers" in url and "totalpricerange" in url \
                    and not captured["pricing"]:
                captured["pricing"] = {"url": url, "headers": dict(req.headers)}

        page.on("request", on_request)

        print(f"  Loading: {event_url}")
        page.goto(event_url, wait_until="load", timeout=45000)
        page.wait_for_timeout(WAIT_MS)

        # Retry once if inventory XHR wasn't captured (TM bot detection during live games)
        if not captured["inventory"]:
            print("  No inventory request captured — retrying in 15s...")
            page.wait_for_timeout(15000)
            captured["inventory"] = None
            captured["pricing"] = None
            page.goto(event_url, wait_until="load", timeout=45000)
            page.wait_for_timeout(WAIT_MS)

        if not captured["inventory"]:
            raise RuntimeError(
                "No inventory request captured after retry — TM may have changed their page."
            )

        def browser_fetch(url: str, headers: dict) -> dict:
            resp = page.request.get(url, headers=headers)
            return resp.json()

        # Fetch all available seats (single call, no pagination needed)
        inv = captured["inventory"]
        inventory_data = browser_fetch(inv["url"], inv["headers"])
        all_facets = inventory_data.get("facets", [])
        total_seats = sum(f.get("count", 0) for f in all_facets)
        print(f"  Found {len(all_facets)} listing groups covering {total_seats} seats.")

        # Build offer→price map
        if captured["pricing"]:
            try:
                pr = captured["pricing"]
                pricing_data = browser_fetch(pr["url"], pr["headers"])
                for facet in pricing_data.get("facets", []):
                    price_ranges = facet.get("totalPriceRange", [])
                    price = price_ranges[0].get("min") if price_ranges else None
                    for offer_id in facet.get("offers", []):
                        offer_price_map[offer_id] = price
            except Exception as e:
                print(f"  Warning: could not fetch price data: {e}")
        else:
            print("  Warning: no pricing request captured — prices will be empty.")

        browser.close()

    print(f"  Price map built for {len(offer_price_map)} offers.")
    return all_facets, offer_price_map


# ── Parsing / output ──────────────────────────────────────────────────────────

def parse_facet(facet: dict, offer_price_map: dict, scraped_at: str) -> list[dict]:
    """
    Convert a full-inventory facet entry into one CSV row per offer_id.
    Each facet = one listing group (section + inventory type + set of seats).
    """
    section   = (facet.get("section") or "").strip()
    quantity  = facet.get("count", 0)
    inv_type  = (facet.get("inventoryTypes") or ["unknown"])[0]
    sel_type  = "resale" if inv_type == "resale" else "standard"
    offer_ids = facet.get("offers", [])

    rows = []
    for offer_id in offer_ids:
        price = offer_price_map.get(offer_id)
        rows.append({
            "offer_id":       offer_id,
            "section":        section,
            "quantity":       quantity,
            "price_usd":      price,
            "selection_type": sel_type,
            "scraped_at":     scraped_at,
        })
    return rows


def save_csv(rows: list[dict], path: str) -> None:
    """Append rows to CSV, writing header only on first run."""
    if not rows:
        print("No rows to save.")
        return
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def print_summary(event: dict, rows: list[dict], output_csv: str) -> None:
    if not rows:
        print("No listings found.")
        return

    prices    = [r["price_usd"] for r in rows if r["price_usd"] is not None]
    sections  = [r["section"]   for r in rows if r["section"]]
    types     = Counter(r["selection_type"] for r in rows)
    top5      = Counter(sections).most_common(5)
    total_seats = sum(r["quantity"] for r in rows if r["quantity"])

    name    = event.get("name", "Game")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "?")
    venue   = event.get("_embedded", {}).get("venues", [{}])[0].get("name", "Arena")

    print()
    print("=" * 54)
    print(f"  {name}")
    print(f"  {venue}  |  {game_dt}")
    print("=" * 54)
    print(f"  Listing groups: {len(rows)}  |  Total seats: {total_seats}")
    print(f"  Resale: {types.get('resale', 0)}  |  Standard: {types.get('standard', 0)}")
    if prices:
        print(f"  Price range:   ${min(prices):.0f} – ${max(prices):.0f}")
        print(f"  Median price:  ${sorted(prices)[len(prices) // 2]:.0f}")
    print()
    print("  Top sections by listing count:")
    for section, count in top5:
        print(f"    Section {section:<8} {count} listings")
    print()
    print(f"  Saved to: {output_csv}")
    print("=" * 54)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    valid_snapshots = ("pre_game", "halftime")
    if len(sys.argv) != 3 or sys.argv[2] not in valid_snapshots:
        print("Usage: python fetch_listings.py <team_slug> [pre_game|halftime]")
        print("  e.g. python fetch_listings.py warriors pre_game")
        sys.exit(1)

    team     = get_team(sys.argv[1])
    snapshot = sys.argv[2]

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{scraped_at}] Team: {team['slug']}  |  Snapshot: {snapshot}")
    print(f"Finding next home game...")

    event   = find_next_home_game(team["tm_keyword"])
    name    = event.get("name", "Game")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "?")
    url     = event.get("url")
    print(f"Found: {name}  ({game_dt})")

    gdir       = game_dir(team["slug"], game_dt, name)
    output_csv = pre_game_csv(gdir) if snapshot == "pre_game" else halftime_csv(gdir)
    os.makedirs(gdir, exist_ok=True)

    if not url:
        raise RuntimeError("Event has no URL in TM Discovery API response.")

    print(f"\nScraping listings from Ticketmaster...")
    facets, offer_price_map = scrape_listings(url)

    rows = []
    for f in facets:
        rows.extend(parse_facet(f, offer_price_map, scraped_at))
    save_csv(rows, output_csv)
    print_summary(event, rows, output_csv)


if __name__ == "__main__":
    main()
