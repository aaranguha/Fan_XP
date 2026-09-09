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

from fetch_listings import scrape_listings, build_rows_from_embedded_offers
from datetime import datetime, timezone


def main():
    if len(sys.argv) != 2:
        print("Usage: python smoke_test_runner.py <event_url>")
        sys.exit(1)

    url = sys.argv[1]
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
