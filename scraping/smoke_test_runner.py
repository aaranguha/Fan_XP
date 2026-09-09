"""
smoke_test_runner.py

One-off health check for a self-hosted scraping runner: launches a real
browser and does a single live scrape against a real Ticketmaster event,
then reports what it found and exits. No sleep_until, no Supabase writes,
no CSV files under data/ -- this never touches production data or state,
it only proves the runner's own environment can reach Ticketmaster and
scrape successfully.

Usage:
    python smoke_test_runner.py <event_url>
"""

import sys

import requests
from fetch_listings import scrape_listings, build_rows_from_embedded_offers
from datetime import datetime, timezone


def check_espn_scoreboard():
    """Confirm this machine can reach ESPN's live scoreboard -- wait_for_halftime()
    already degrades gracefully to a fixed-time fallback if this is blocked, so
    this isn't fatal either way, but it affects whether the halftime scrape
    fires at the real Q2-under-2-min moment or just a fixed 70 min after kickoff."""
    try:
        today = datetime.now().strftime("%Y%m%d")
        resp = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={today}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        print(f"[smoke test] ESPN scoreboard: HTTP {resp.status_code}")
        if resp.status_code == 200:
            events = resp.json().get("events", [])
            print(f"[smoke test] ESPN reachable -- {len(events)} events returned. Live halftime detection will work.")
        else:
            print(f"[smoke test] ESPN blocked (HTTP {resp.status_code}) -- halftime will use the 70-min fallback timer instead of live detection. Not fatal.")
    except Exception as e:
        print(f"[smoke test] ESPN check failed: {e} -- halftime will use the 70-min fallback timer instead. Not fatal.")


def main():
    if len(sys.argv) != 2:
        print("Usage: python smoke_test_runner.py <event_url>")
        sys.exit(1)

    url = sys.argv[1]
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    check_espn_scoreboard()

    print(f"[smoke test] Scraping {url}")
    try:
        facets, offer_price_map, places_facets, embedded_offers = scrape_listings(
            url, max_retries=1, team_slug="smoke_test"
        )
    except Exception as e:
        print(f"[smoke test] SCRAPE FAILED: {e}")
        sys.exit(1)

    rows = build_rows_from_embedded_offers(embedded_offers, scraped_at)
    priced = [r for r in rows if r.get("price_usd") is not None]

    print(f"[smoke test] facets={len(facets)} places_facets={len(places_facets)} embedded_offers={len(embedded_offers)}")
    print(f"[smoke test] rows built: {len(rows)}, priced: {len(priced)}")

    if not rows:
        print("[smoke test] FAIL: no rows built at all.")
        sys.exit(1)
    if len(priced) < len(rows):
        print(f"[smoke test] WARNING: {len(rows) - len(priced)} rows missing a price.")

    print("[smoke test] PASS: runner can reach Ticketmaster and scrape real listings.")
    for r in rows[:5]:
        print(f"  sample: {r}")


if __name__ == "__main__":
    main()
